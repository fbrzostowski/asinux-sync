"""Orchestrates capture -> bundle -> push and pull -> apply.

The engine deliberately knows nothing about GSettings/dconf/D-Bus - that's
entirely inside each SyncModule. It only moves a `profile.json` + an
`assets/` directory of content-addressed files between a local staging dir
and `remote:asinux-sync/profile/` via the rclone-backed CloudBackend.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .cloud.rclone import RcloneBackend
from .modules.base import SyncModule

PROFILE_FILENAME = "profile.json"
STATE_FILENAME = "state.json"
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

    # -- push -------------------------------------------------------------

    def push(self, enabled_module_ids: list[str], *, force: bool = False) -> SyncResult:
        if not force:
            self._check_for_conflict()

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        profile = {
            "version": PROFILE_VERSION,
            "updated_at": _now_iso(),
            "updated_by_device": socket.gethostname(),
            "modules": {},
        }
        for module_id in enabled_module_ids:
            module = self.modules[module_id]
            profile["modules"][module_id] = module.capture(self.assets_dir)

        profile_path = self.staging_dir / PROFILE_FILENAME
        profile_path.write_text(json.dumps(profile, indent=2))

        self.backend.sync(str(self.staging_dir), self.remote_path, direction="push")
        self._save_state(profile["updated_at"], profile["updated_by_device"])
        return SyncResult(profile=profile, applied_modules=list(profile["modules"]))

    # -- pull -------------------------------------------------------------

    def pull(self, enabled_module_ids: list[str] | None = None) -> SyncResult | None:
        """Download the remote profile and apply it locally.

        `enabled_module_ids` is this *device's* local allowlist (derived from
        Config.enabled()) - a module present in the remote profile but
        disabled on this device is downloaded (so re-enabling it later needs
        no extra push) but deliberately NOT applied. This is what lets one
        machine opt a category like `background` out of sync entirely and
        keep its own local wallpaper across pulls.
        """
        self.backend.sync(str(self.staging_dir), self.remote_path, direction="pull")
        profile_path = self.staging_dir / PROFILE_FILENAME
        if not profile_path.exists():
            return None  # nothing has ever been pushed for this user yet

        profile = json.loads(profile_path.read_text())
        applied = []
        for module_id, data in profile.get("modules", {}).items():
            if enabled_module_ids is not None and module_id not in enabled_module_ids:
                continue  # disabled on this device - leave local state alone
            module = self.modules.get(module_id)
            if module is None:
                continue  # unknown module (older client / newer profile) - skip, don't fail
            module.apply(data, self.assets_dir)
            applied.append(module_id)

        self._save_state(profile["updated_at"], profile["updated_by_device"])
        return SyncResult(profile=profile, applied_modules=applied)

    # -- conflict detection (v1: per-profile, not per-module) --------------

    def _check_for_conflict(self) -> None:
        state = self._load_state()
        if state is None:
            return  # first push ever from this device, nothing to compare against

        if not self.backend.exists_file(f"{self.remote_path}/{PROFILE_FILENAME}"):
            return  # nothing pushed remotely yet

        remote_profile = json.loads(self.backend.cat(f"{self.remote_path}/{PROFILE_FILENAME}"))
        remote_updated_at = remote_profile.get("updated_at")
        if remote_updated_at and remote_updated_at != state["last_synced_updated_at"]:
            raise ConflictError(remote_updated_at, remote_profile.get("updated_by_device", "unknown"))

    def _state_path(self) -> Path:
        return self.state_dir / STATE_FILENAME

    def _load_state(self) -> dict | None:
        path = self._state_path()
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def _save_state(self, updated_at: str, updated_by_device: str) -> None:
        self._state_path().write_text(
            json.dumps({"last_synced_updated_at": updated_at, "last_synced_by_device": updated_by_device})
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
