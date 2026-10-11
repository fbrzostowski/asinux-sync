"""Background sync loop run by the asinux-sync systemd user service.

It has to be a *user* service, not a system one: everything it syncs lives
in the user's own dconf database and home directory.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from pathlib import Path

from . import config as config_module
from . import registry as registry_module
from .cloud.rclone import RcloneBackend, RcloneError, is_installed
from .engine import SyncEngine
from .modules import REGISTRY

logger = logging.getLogger("asinux-sync")

STATUS_PATH = config_module.STATE_DIR / "status.json"
POLL_SECONDS = 5


def tick(cfg: config_module.Config) -> str:
    """One heartbeat + sync cycle. Returns the result word for the status file."""
    if not cfg.device_id or not is_installed() or not RcloneBackend.is_configured(cfg.remote_name):
        return "not-configured"

    backend = RcloneBackend(cfg.remote_name)
    # Heartbeat first: it also adopts this device's module toggles, which may
    # have been changed from another device's panel.
    registry_module.register_this_device(registry_module.Registry(backend), cfg)

    modules = {mid: cls() for mid, cls in REGISTRY.items()}
    engine = SyncEngine(backend, modules, config_module.STATE_DIR)
    return engine.sync_once(cfg.enabled(list(REGISTRY)))


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
