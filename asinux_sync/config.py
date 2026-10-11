"""Local, per-device config: which rclone remote to use and who this device is.

Lives at ~/.config/asinux-sync/config.json - local wiring, not part of the
synced profile. `disabled_modules` is a cache of this device's entry in the
cloud device registry (see registry.py), so the CLI keeps working offline.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "asinux-sync"
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_DIR = Path.home() / ".local" / "share" / "asinux-sync"

MIN_INTERVAL_SECONDS = 60


@dataclass
class Config:
    remote_name: str = "asinux-cloud"
    device_id: str = ""
    device_name: str = ""
    # Switch-off list: "<module>" for a whole module, "<module>.<item>" for one
    # item of it. A cache of this device's entry in the cloud registry.
    disabled_modules: list[str] = field(default_factory=list)
    # Modules that start switched off whose default has been applied to this
    # device already, so turning one on later isn't undone.
    defaults_applied: list[str] = field(default_factory=list)
    background_sync: bool = True
    sync_interval_seconds: int = 60


def load() -> Config:
    if not CONFIG_PATH.exists():
        return Config()
    known = {f.name for f in fields(Config)}
    raw = json.loads(CONFIG_PATH.read_text())
    return Config(**{k: v for k, v in raw.items() if k in known})


def save(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2))
