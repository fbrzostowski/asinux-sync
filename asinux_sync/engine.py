"""Orchestrates capture -> bundle -> push and pull -> apply.

The engine deliberately knows nothing about GSettings/dconf/D-Bus - that's
entirely inside each SyncModule. It only moves a `profile.json` + an
`assets/` directory of content-addressed files between a local staging dir
and `remote:asinux-sync/profile/` via the rclone-backed CloudBackend.

Sync decisions are made per item (one theme part, one extension, ...), by
comparing three things: the item here now, the item in the cloud now, and
what both looked like at this device's last sync. That is what lets a change
you make on this computer be uploaded instead of overwritten by the cloud's
older value, and a change made elsewhere be downloaded without disturbing the
settings you changed here.

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

        Returns "pushed" (something was uploaded), "pulled" (something was
        applied here) or "idle". For every item, comparing here-now,
        cloud-now and what each was at the last sync (the "seen" pair):

        - never synced here, cloud has it  -> apply it (the cloud wins: a new
          device, or a category just switched on, must not overwrite what
          other devices share)
        - never synced here, cloud lacks it -> upload it, unless there is
          nothing to share: this is the device's first sync (a new
          installation, whose values are installer defaults) or the item is
          still entirely at its defaults. Those are only remembered, so
          that what is changed later is uploaded.
        - changed here (whatever the cloud did) -> upload it. A change made
          on this computer is never overwritten by the cloud's older value;
          if both sides changed the same item, this device wins, since
          changes are noticed within one interval and this one is recent.
        - only the cloud changed            -> apply it
        - neither                           -> nothing

        Items are independent: a change to one never causes another to be
        dropped or overwritten.

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

            local_items = _flat(self._capture(disabled))
            cloud_items = _flat(remote["modules"]) if remote else {}
            # With nothing readable in the cloud there is no baseline to compare
            # against: everything here counts as never synced, so all of it goes up.
            known = (state or {}).get("items", {}) if remote is not None else {}

            # A device that has never synced is a new installation: what it has
            # now are its installer defaults, not choices to share.
            first_sync = remote is not None and state is None

            to_push: dict[str, dict] = {}
            to_apply: dict[str, dict] = {}
            baseline: dict[str, dict] = {}
            for key in sorted(set(local_items) | {k for k in cloud_items if self._is_enabled(k, disabled)}):
                here = _digest(local_items[key]) if key in local_items else None
                cloud = _digest(cloud_items[key]) if key in cloud_items else None
                seen = known.get(key)
                if seen is None:
                    if cloud is not None:
                        to_apply[key] = cloud_items[key]
                    elif here is not None:
                        module_id, _, item_id = key.partition(".")
                        if first_sync or self.modules[module_id].is_default(item_id, local_items[key]):
                            # Nothing to share yet: remember how it looks now, so
                            # that only what changes later is uploaded.
                            baseline[key] = {"local": here, "cloud": None}
                        else:
                            to_push[key] = local_items[key]
                elif here is not None and here != seen["local"]:
                    to_push[key] = local_items[key]
                elif cloud is not None and cloud != seen["cloud"]:
                    to_apply[key] = cloud_items[key]

            new_items = {**known, **baseline}
            profile = remote
            if to_apply:
                self._apply_items(to_apply, disabled)
                after = _flat(self._capture(disabled))
                for key, data in to_apply.items():
                    new_items[key] = {"local": _digest(after[key]) if key in after else None, "cloud": _digest(data)}
            if to_push:
                profile = self._upload(remote, to_push)
                merged = _flat(profile["modules"])
                for key, data in to_push.items():
                    new_items[key] = {"local": _digest(data), "cloud": _digest(merged[key])}
            if profile is not None:
                self._save_state(profile, new_items)
            return "pushed" if to_push else "pulled" if to_apply else "idle"

    def cloud_has_item(self, key: str) -> bool:
        """Does the cloud profile already hold "module.item"?"""
        with self._locked():
            remote = self._download()
            return remote is not None and remote.get("version", 1) >= PROFILE_VERSION and key in _flat(remote["modules"])

    def push(self, disabled: Collection[str] = (), *, force: bool = False) -> SyncResult:
        """Upload everything switched on here, whatever the cloud has."""
        disabled = set(disabled)
        with self._locked():
            remote = self._download()
            if remote is not None and not force:
                self._check_for_conflict(remote)
            local_items = _flat(self._capture(disabled))
            profile = self._upload(remote, local_items)
            merged = _flat(profile["modules"])
            items = (self._load_state() or {}).get("items", {})
            for key, data in local_items.items():
                items[key] = {"local": _digest(data), "cloud": _digest(merged[key])}
            self._save_state(profile, items)
            return SyncResult(profile=profile, applied_modules=sorted({k.partition(".")[0] for k in local_items}))

    def pull(self, disabled: Collection[str] = ()) -> SyncResult | None:
        """Download the remote profile and apply it locally. Whatever is on
        this device's switch-off list is downloaded but deliberately NOT
        applied - that is what lets one machine keep its own wallpaper."""
        disabled = set(disabled)
        with self._locked():
            remote = self._download()
            if remote is None or remote.get("version", 1) < PROFILE_VERSION:
                return None  # nothing (readable) has been pushed yet
            cloud_items = {k: v for k, v in _flat(remote["modules"]).items() if self._is_enabled(k, disabled)}
            self._apply_items(cloud_items, disabled)
            after = _flat(self._capture(disabled))
            items = (self._load_state() or {}).get("items", {})
            for key, data in cloud_items.items():
                items[key] = {"local": _digest(after[key]) if key in after else None, "cloud": _digest(data)}
            self._save_state(remote, items)
            return SyncResult(profile=remote, applied_modules=sorted({k.partition(".")[0] for k in cloud_items}))

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

    def _is_enabled(self, key: str, disabled: set[str]) -> bool:
        module_id = key.partition(".")[0]
        return module_id in self.modules and module_id not in disabled and key not in disabled

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

    def _apply_items(self, items: dict[str, dict], disabled: set[str]) -> None:
        """Apply the given items ("module.item" -> data), module by module in
        our own registry order - the intended apply order, not the profile's."""
        by_module: dict[str, dict] = {}
        for key, data in items.items():
            module_id, _, item_id = key.partition(".")
            by_module.setdefault(module_id, {})[item_id] = data
        for module_id, module in self.modules.items():
            if module_id not in by_module:
                continue
            try:
                module.apply({"items": by_module[module_id]}, self.assets_dir, self._disabled_items(module_id, disabled))
            except ModuleUnavailable as exc:
                logger.info("%s unavailable here: %s", module_id, exc)
            except Exception:
                logger.exception("applying %s failed, skipping it", module_id)

    def _upload(self, remote: dict | None, items: dict[str, dict]) -> dict:
        """Merge `items` into the cloud's profile and upload it. Whatever this
        device isn't uploading (switched off, unavailable, not installed here,
        unchanged) stays as the cloud has it."""
        modules = {mid: {"items": dict(m.get("items", {}))} for mid, m in (remote or {}).get("modules", {}).items()}
        for key, data in items.items():
            module_id, _, item_id = key.partition(".")
            cloud_items = modules.setdefault(module_id, {"items": {}})["items"]
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

    def _save_state(self, profile: dict, items: dict[str, dict]) -> None:
        """`items` maps "module.item" to the digests of that item here and in
        the cloud at this sync - the baseline for the next cycle's comparison."""
        self._state_path().write_text(
            json.dumps(
                {
                    "last_synced_revision": profile.get("revision"),
                    "last_synced_updated_at": profile["updated_at"],
                    "last_synced_by_device": profile["updated_by_device"],
                    "items": items,
                }
            )
        )


def _flat(modules: dict) -> dict[str, dict]:
    """{"module": {"items": {"item": data}}} -> {"module.item": data}"""
    return {f"{mid}.{iid}": data for mid, module in modules.items() for iid, data in module.get("items", {}).items()}


def _digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
