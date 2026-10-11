"""Orchestrates capture -> bundle -> push and pull -> apply.

The engine deliberately knows nothing about GSettings/dconf/D-Bus - that's
entirely inside each SyncModule. It only moves a `profile.json` + an
`assets/` directory of content-addressed files between a local staging dir
and `remote:asinux-sync/profile/` via the rclone-backed CloudBackend.

Every public operation holds a cross-process lock, because the background
service and the settings panel share one staging directory.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import socket
import uuid
from collections.abc import Collection
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .cloud.rclone import RcloneBackend
from .modules.base import ModuleUnavailable, SyncModule

logger = logging.getLogger(__name__)

PROFILE_FILENAME = "profile.json"
STATE_FILENAME = "state.json"
LOCK_FILENAME = "lock"
PROFILE_VERSION = 2  # 2: modules hold {"items": {...}}; version 1 profiles can't be read


class ConflictError(RuntimeError):
    """Raised by push() when the remote profile changed since our last
    known sync point and we haven't pushed those changes ourselves - i.e.
    another device has unseen changes that a plain push would clobber."""

    def __init__(self, remote_updated_at: str, remote_updated_by: str):
        super().__init__(
            f"remote profile was updated at {remote_updated_at} by {remote_updated_by} "
            "since this device's last sync - pass force=True to overwrite anyway"
        )
        self.remote_updated_at = remote_updated_at
        self.remote_updated_by = remote_updated_by


@dataclass
class SyncResult:
    profile: dict
    applied_modules: list[str] = field(default_factory=list)


class SyncEngine:
    def __init__(
        self,
        backend: RcloneBackend,
        modules: dict[str, SyncModule],
        state_dir: Path,
    ):
        self.backend = backend
        self.modules = modules
        self.state_dir = state_dir
        self.staging_dir = state_dir / "staging"
        self.assets_dir = self.staging_dir / "assets"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def remote_path(self) -> str:
        return "asinux-sync/profile"

    # -- public operations ------------------------------------------------
    #
    # `disabled` is this device's switch-off list: "<module>" turns a whole
    # module off, "<module>.<item>" one item of it.

    def sync_once(self, disabled: Collection[str] = ()) -> str:
        """One two-way sync cycle, as run by the background service.

        Returns "pushed", "pulled" or "idle". Decision table:

        - nothing in the cloud yet                 -> push
        - first sync of this device, cloud has data -> pull (the cloud wins,
          a fresh device must never overwrite the shared profile)
        - only the cloud changed since last sync    -> pull
        - only this device changed                  -> push
        - both changed                              -> push (this device
          wins). Changes are noticed within one interval, so a local change
          is at most that old and is the most recent edit.
        - neither                                   -> idle

        A cloud profile in an older format is replaced by the first device
        that has already synced (it migrates it); a brand-new device leaves
        it alone and waits, so it can't wipe settings it can't read.
        """
        disabled = set(disabled)
        with self._locked():
            remote = self._download()
            state = self._load_state()
            if remote is not None and remote.get("version", 1) < PROFILE_VERSION:
                if state is None:
                    return "idle"
                remote = None
            local = self._capture(disabled)
            digest = _digest(local)

            if remote is None:
                self._upload(None, local, digest)
                return "pushed"
            if state is None:
                self._apply(remote, disabled)
                self._save_state(remote, _digest(self._capture(disabled)))
                return "pulled"

            remote_changed = remote.get("revision") != state.get("last_synced_revision")
            local_changed = digest != state.get("last_digest")
            if local_changed:
                self._upload(remote, local, digest)
                return "pushed"
            if remote_changed:
                self._apply(remote, disabled)
                self._save_state(remote, _digest(self._capture(disabled)))
                return "pulled"
            return "idle"

    def push(self, disabled: Collection[str] = (), *, force: bool = False) -> SyncResult:
        disabled = set(disabled)
        with self._locked():
            remote = self._download()
            if remote is not None and not force:
                self._check_for_conflict(remote)
            local = self._capture(disabled)
            profile = self._upload(remote, local, _digest(local))
            return SyncResult(profile=profile, applied_modules=list(local))

    def pull(self, disabled: Collection[str] = ()) -> SyncResult | None:
        """Download the remote profile and apply it locally. Whatever is on
        this device's switch-off list is downloaded but deliberately NOT
        applied - that is what lets one machine keep its own wallpaper."""
        disabled = set(disabled)
        with self._locked():
            remote = self._download()
            if remote is None or remote.get("version", 1) < PROFILE_VERSION:
                return None  # nothing (readable) has been pushed yet
            applied = self._apply(remote, disabled)
            self._save_state(remote, _digest(self._capture(disabled)))
            return SyncResult(profile=remote, applied_modules=applied)

    # -- building blocks (call with the lock held) -------------------------

    def _download(self) -> dict | None:
        """Mirror the cloud profile into the staging dir and return it."""
        if not self.backend.exists_dir(self.remote_path):
            return None
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backend.sync(str(self.staging_dir), self.remote_path, direction="pull")
        profile_path = self.staging_dir / PROFILE_FILENAME
        if not profile_path.exists():
            return None
        return json.loads(profile_path.read_text())

    @staticmethod
    def _disabled_items(module_id: str, disabled: set[str]) -> set[str]:
        prefix = f"{module_id}."
        return {key[len(prefix):] for key in disabled if key.startswith(prefix)}

    def _capture(self, disabled: set[str]) -> dict:
        """Capture each module that is switched on, leaving out any that
        can't be captured here.

        A module (or item) missing from the result is simply not touched in
        the cloud profile (see _upload), so one broken or unavailable module
        never blocks - or wipes - the rest."""
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        captured = {}
        for module_id, module in self.modules.items():
            if module_id in disabled:
                continue
            try:
                captured[module_id] = module.capture(self.assets_dir, self._disabled_items(module_id, disabled))
            except ModuleUnavailable as exc:
                logger.info("%s unavailable here: %s", module_id, exc)
            except Exception:
                logger.exception("capturing %s failed, skipping it", module_id)
        return captured

    def _apply(self, profile: dict, disabled: set[str]) -> list[str]:
        applied = []
        profile_modules = profile.get("modules", {})
        # Our own registry order, not the profile's: it is the intended apply order.
        for module_id, module in self.modules.items():
            if module_id not in profile_modules or module_id in disabled:
                continue
            try:
                module.apply(profile_modules[module_id], self.assets_dir, self._disabled_items(module_id, disabled))
            except ModuleUnavailable as exc:
                logger.info("%s unavailable here: %s", module_id, exc)
                continue
            except Exception:
                logger.exception("applying %s failed, skipping it", module_id)
                continue
            applied.append(module_id)
        return applied

    def _upload(self, remote: dict | None, local: dict, digest: str) -> dict:
        # Merge item by item into the cloud's profile: whatever this device
        # didn't capture (switched off, unavailable, not installed here)
        # stays as the other devices left it.
        modules = {mid: {"items": dict(m.get("items", {}))} for mid, m in (remote or {}).get("modules", {}).items()}
        for module_id, captured in local.items():
            cloud_items = modules.setdefault(module_id, {"items": {}})["items"]
            for item_id, data in captured.get("items", {}).items():
                existing = cloud_items.get(item_id)
                cloud_items[item_id] = self.modules[module_id].merge_item(item_id, existing, data) if existing else data
        profile = {
            "version": PROFILE_VERSION,
            "updated_at": _now_iso(),
            # A timestamp alone can't tell two uploads in the same second apart.
            "revision": uuid.uuid4().hex,
            "updated_by_device": socket.gethostname(),
            "modules": modules,
        }
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        (self.staging_dir / PROFILE_FILENAME).write_text(json.dumps(profile, indent=2))
        self.backend.sync(str(self.staging_dir), self.remote_path, direction="push")
        self._save_state(profile, digest)
        return profile

    # -- conflict detection (manual push only) ------------------------------

    def _check_for_conflict(self, remote: dict) -> None:
        state = self._load_state()
        if state is None:
            return  # first push ever from this device, nothing to compare against
        if remote.get("revision") != state.get("last_synced_revision"):
            raise ConflictError(remote.get("updated_at", "unknown"), remote.get("updated_by_device", "unknown"))

    # -- local state ---------------------------------------------------------

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / LOCK_FILENAME, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _state_path(self) -> Path:
        return self.state_dir / STATE_FILENAME

    def _load_state(self) -> dict | None:
        path = self._state_path()
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def _save_state(self, profile: dict, digest: str) -> None:
        self._state_path().write_text(
            json.dumps(
                {
                    "last_synced_revision": profile.get("revision"),
                    "last_synced_updated_at": profile["updated_at"],
                    "last_synced_by_device": profile["updated_by_device"],
                    "last_digest": digest,
                }
            )
        )


def _digest(modules: dict) -> str:
    return hashlib.sha256(json.dumps(modules, sort_keys=True).encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
