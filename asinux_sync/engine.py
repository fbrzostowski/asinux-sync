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
import socket
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .cloud.rclone import RcloneBackend
from .modules.base import SyncModule

PROFILE_FILENAME = "profile.json"
STATE_FILENAME = "state.json"
LOCK_FILENAME = "lock"
PROFILE_VERSION = 1


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

    def sync_once(self, enabled_module_ids: list[str]) -> str:
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
        """
        with self._locked():
            remote = self._download()
            state = self._load_state()
            local = self._capture(enabled_module_ids)
            digest = _digest(local)

            if remote is None:
                self._upload(remote, local, digest)
                return "pushed"
            if state is None:
                self._apply(remote, enabled_module_ids)
                self._save_state(remote, _digest(self._capture(enabled_module_ids)))
                return "pulled"

            remote_changed = remote.get("revision") != state.get("last_synced_revision")
            local_changed = digest != state.get("last_digest")
            if local_changed:
                self._upload(remote, local, digest)
                return "pushed"
            if remote_changed:
                self._apply(remote, enabled_module_ids)
                self._save_state(remote, _digest(self._capture(enabled_module_ids)))
                return "pulled"
            return "idle"

    def push(self, enabled_module_ids: list[str], *, force: bool = False) -> SyncResult:
        with self._locked():
            remote = self._download()
            if remote is not None and not force:
                self._check_for_conflict(remote)
            local = self._capture(enabled_module_ids)
            profile = self._upload(remote, local, _digest(local))
            return SyncResult(profile=profile, applied_modules=list(local))

    def pull(self, enabled_module_ids: list[str] | None = None) -> SyncResult | None:
        """Download the remote profile and apply it locally.

        `enabled_module_ids` is this *device's* allowlist (derived from
        Config.enabled()) - a module present in the remote profile but
        disabled on this device is downloaded but deliberately NOT applied.
        This is what lets one machine opt a category like `background` out of
        sync entirely and keep its own local wallpaper across pulls.
        """
        with self._locked():
            remote = self._download()
            if remote is None:
                return None  # nothing has ever been pushed yet
            applied = self._apply(remote, enabled_module_ids)
            ids = enabled_module_ids if enabled_module_ids is not None else list(self.modules)
            self._save_state(remote, _digest(self._capture(ids)))
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

    def _capture(self, module_ids: list[str]) -> dict:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        return {mid: self.modules[mid].capture(self.assets_dir) for mid in module_ids if mid in self.modules}

    def _apply(self, profile: dict, enabled_module_ids: list[str] | None) -> list[str]:
        applied = []
        for module_id, data in profile.get("modules", {}).items():
            if enabled_module_ids is not None and module_id not in enabled_module_ids:
                continue  # disabled on this device - leave local state alone
            module = self.modules.get(module_id)
            if module is None:
                continue  # unknown module (older client / newer profile) - skip, don't fail
            module.apply(data, self.assets_dir)
            applied.append(module_id)
        return applied

    def _upload(self, remote: dict | None, local: dict, digest: str) -> dict:
        # Start from the cloud's modules so categories this device has
        # disabled (and therefore didn't capture) are kept for other devices.
        modules = dict(remote.get("modules", {})) if remote else {}
        modules.update(local)
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
