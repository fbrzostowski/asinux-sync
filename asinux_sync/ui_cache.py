"""What the panel last saw of the cloud (account + devices), kept locally so
the panel can show it instantly on launch while fresh data loads."""

from __future__ import annotations

import json

from . import config as config_module


def _path():
    return config_module.STATE_DIR / "ui-cache.json"


def load() -> dict | None:
    """{"fetched_at", "account", "devices"} or None if nothing was cached yet."""
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "devices" in data else None


def save(account: dict, devices: list[dict], fetched_at: str) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"fetched_at": fetched_at, "account": account, "devices": devices}))
    temp.replace(path)
