"""Background sync loop run by the asinux-sync systemd user service.

It has to be a *user* service, not a system one: everything it syncs lives
in the user's own dconf database and home directory.
"""

from __future__ import annotations

import json
import logging
import signal
import subprocess
import tarfile
import sys
import threading
import time
from pathlib import Path

from . import config as config_module
from . import device_requests, missing_extensions, theme_requests, wallpaper_requests
from . import registry as registry_module
from .cloud.rclone import RcloneBackend, RcloneError, is_installed
from .engine import SyncEngine
from .modules import REGISTRY, shell_extensions

logger = logging.getLogger("asinux-sync")

STATUS_PATH = config_module.STATE_DIR / "status.json"
POLL_SECONDS = 5


PROMPT_COOLDOWN_SECONDS = 3600
REPO_DIR = Path(__file__).resolve().parent.parent


def _launch_prompt(request_path: Path, result_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "asinux_sync_settings.extension_prompt", str(request_path), str(result_path)],
        cwd=REPO_DIR,
    )


class ExtensionPrompter:
    """Asks, in a window of its own, about extensions the cloud has turned on
    but this computer lacks. The service never waits for the answer: the
    window runs as a separate process and is checked on later cycles."""

    def __init__(self, launch=_launch_prompt, installed=shell_extensions.installed, clock=time.monotonic):
        self.launch = launch
        self.installed = installed
        self.clock = clock
        self.process: subprocess.Popen | None = None
        self.not_before = 0.0

    @staticmethod
    def _paths() -> tuple[Path, Path]:
        folder = config_module.STATE_DIR / "prompt"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "request.json", folder / "result.json"

    def check(self, cfg: config_module.Config, engine: SyncEngine, backend: RcloneBackend) -> None:
        self._collect(cfg, engine)
        if self.process is not None or self.clock() < self.not_before:
            return
        missing = missing_extensions.find_missing(
            engine.last_remote, cfg.disabled_modules, cfg.declined_extensions, self.installed())
        if not missing:
            return
        names = missing_extensions.names_from_devices(registry_module.Registry(backend).list_devices())
        request, result = self._paths()
        result.unlink(missing_ok=True)
        request.write_text(json.dumps({"extensions": [{"uuid": u, "name": names.get(u, u)} for u in missing]}))
        logger.info("asking about missing extensions: %s", ", ".join(missing))
        self.process = self.launch(request, result)

    def _collect(self, cfg: config_module.Config, engine: SyncEngine) -> None:
        if self.process is None or self.process.poll() is None:
            return  # nothing asked, or the user is still looking at the window
        returncode, self.process = self.process.returncode, None
        request, result = self._paths()
        request.unlink(missing_ok=True)
        if not result.exists():
            # Closed without choosing (or no display): ask again, but not at once.
            self.not_before = self.clock() + PROMPT_COOLDOWN_SECONDS
            logger.info("extension prompt ended without an answer (exit %s)", returncode)
            return
        answer = json.loads(result.read_text())
        result.unlink(missing_ok=True)
        cfg.declined_extensions = sorted({*cfg.declined_extensions, *answer.get("declined", [])})
        config_module.save(cfg)
        for uuid, reason in answer.get("failed", {}).items():
            logger.warning("could not install %s: %s", uuid, reason)
        installed = answer.get("installed", [])
        if installed:
            # Their cloud values were skipped while they were missing: apply them next cycle.
            engine.forget_items(
                [f"extensions.{u}" for u in installed] + [f"extensions.{u}#prefs" for u in installed]
                # layout: extension settings; shell_theme: needs the User Themes extension.
                + ["layout.all", "appearance.shell_theme"])


_prompter = ExtensionPrompter()
# Every kind of thing devices can ask each other for (see device_requests.py).
_requests = device_requests.RequestBoard([theme_requests.ThemeHandler(), wallpaper_requests.WallpaperHandler()])


def tick(cfg: config_module.Config) -> str:
    """One heartbeat + sync cycle. Returns the result word for the status file."""
    if not cfg.device_id or not is_installed() or not RcloneBackend.is_configured(cfg.remote_name):
        return "not-configured"

    backend = RcloneBackend(cfg.remote_name)
    # Heartbeat first: it also adopts this device's module toggles, which may
    # have been changed from another device's panel.
    modules = {mid: cls() for mid, cls in REGISTRY.items()}
    registry = registry_module.Registry(backend)
    try:
        registry_module.register_this_device(registry, cfg, modules)
    except registry_module.DeviceRemoved:
        logger.info("this device was removed from the account - no longer syncing")
        registry_module.forget_this_device(registry, cfg)
        return "removed"

    engine = SyncEngine(backend, modules, config_module.STATE_DIR)
    result = engine.sync_once(cfg.disabled_modules)
    _prompter.check(cfg, engine, backend)
    try:
        _requests.check(cfg, engine, backend, registry)
    except (RcloneError, OSError, ValueError, tarfile.TarError) as exc:
        # Asking for or sending something must not mark the whole sync as failed.
        logger.warning("device requests: %s", exc)
    return result


def read_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write_status(result: str, error: str | None) -> None:
    config_module.STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps({"last_run": registry_module.now_iso(), "result": result, "error": error}))


def _config_mtime() -> float:
    try:
        return config_module.CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def run(stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}

    try:
        _loop(stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _loop(stop: threading.Event) -> None:
    logger.info("asinux-sync daemon started")
    while not stop.is_set():
        cfg = config_module.load()
        try:
            result = tick(cfg)
            _write_status(result, None)
            if result in ("pushed", "pulled"):
                logger.info("sync: %s", result)
        except (RcloneError, OSError, ValueError, KeyError) as exc:
            logger.warning("sync failed: %s", exc)
            _write_status("error", str(exc))

        interval = max(config_module.MIN_INTERVAL_SECONDS, cfg.sync_interval_seconds)
        # tick() may itself rewrite config.json (device toggles), so remember
        # the mtime *after* it and wake early only on someone else's edit
        # (e.g. the panel changing the interval or a module toggle).
        seen_mtime = _config_mtime()
        waited = 0
        while waited < interval and not stop.is_set():
            if stop.wait(POLL_SECONDS):
                break
            waited += POLL_SECONDS
            if _config_mtime() != seen_mtime:
                break
    logger.info("asinux-sync daemon stopped")
