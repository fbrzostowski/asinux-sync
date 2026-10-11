"""Account and device registry stored in the cloud under `asinux-sync/`.

Layout:
  asinux-sync/account.json          username, display name, locale defaults
  asinux-sync/devices/<id>.json     one file per device (name, disabled modules)
  asinux-sync/profile/              the synced profile (engine.py owns this
                                    subtree - it is mirrored with `rclone sync`,
                                    so nothing else may live inside it)
"""

from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timezone

from . import config as config_module
from .cloud.rclone import RcloneBackend, RcloneError

ROOT = "asinux-sync"
ACCOUNT_PATH = f"{ROOT}/account.json"
DEVICES_DIR = f"{ROOT}/devices"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, backend: RcloneBackend):
        self.backend = backend

    def root_exists(self) -> bool:
        return self.backend.exists_dir(ROOT)

    def load_account(self) -> dict | None:
        if not self.backend.exists_file(ACCOUNT_PATH):
            return None
        return json.loads(self.backend.cat(ACCOUNT_PATH))

    def save_account(self, account: dict) -> None:
        self.backend.rcat(ACCOUNT_PATH, json.dumps(account, indent=2).encode())

    def list_devices(self) -> list[dict]:
        devices = []
        for entry in self.backend.lsjson(DEVICES_DIR):
            if entry.get("IsDir") or not entry["Name"].endswith(".json"):
                continue
            try:
                devices.append(json.loads(self.backend.cat(f"{DEVICES_DIR}/{entry['Name']}")))
            except (RcloneError, ValueError):
                continue
        devices.sort(key=lambda d: d.get("name", "").lower())
        return devices

    def get_device(self, device_id: str) -> dict | None:
        path = f"{DEVICES_DIR}/{device_id}.json"
        if not self.backend.exists_file(path):
            return None
        return json.loads(self.backend.cat(path))

    def save_device(self, device: dict) -> None:
        self.backend.rcat(f"{DEVICES_DIR}/{device['id']}.json", json.dumps(device, indent=2).encode())

    def set_device_module(self, device_id: str, key: str, enabled: bool) -> dict:
        """Switch a module ("background") or one item of it ("appearance.icon_theme") on or off."""
        device = self.get_device(device_id)
        if device is None:
            raise RcloneError("device is no longer registered")
        disabled = set(device.get("disabled_modules", []))
        (disabled.discard if enabled else disabled.add)(key)
        device["disabled_modules"] = sorted(disabled)
        self.save_device(device)
        return device


def build_catalog(modules: dict) -> dict:
    """What can be switched off on this device: module -> its items.

    Stored in the device's registry entry so another device's panel can show
    this device's items (e.g. its extensions), not its own."""
    catalog = {}
    for module_id, module in modules.items():
        try:
            items = module.list_items()
        except Exception:  # a broken module must not stop the heartbeat
            continue
        catalog[module_id] = [{"id": i.id, "name": i.name, "description": i.description} for i in items]
    return catalog


def register_this_device(registry: Registry, cfg: config_module.Config, modules: dict | None = None) -> None:
    """Create or refresh this device's registry entry and persist cfg.

    The cloud entry is the source of truth for `disabled_modules` (it can be
    edited from another device), so an existing entry's list wins over the
    local cache.
    """
    if not cfg.device_id:
        cfg.device_id = uuid.uuid4().hex
    existing = registry.get_device(cfg.device_id)
    if existing is not None:
        cfg.disabled_modules = list(existing.get("disabled_modules", []))
    registry.save_device(
        {
            "id": cfg.device_id,
            "name": cfg.device_name,
            "hostname": socket.gethostname(),
            "disabled_modules": cfg.disabled_modules,
            "catalog": build_catalog(modules) if modules is not None else (existing or {}).get("catalog", {}),
            "last_seen": now_iso(),
        }
    )
    config_module.save(cfg)
