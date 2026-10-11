"""Control of the asinux-sync systemd *user* service."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "asinux-sync.service"
REPO_DIR = Path(__file__).resolve().parent.parent
UNIT_TEMPLATE = REPO_DIR / "systemd" / UNIT_NAME
USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


class ServiceError(RuntimeError):
    pass


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    if shutil.which("systemctl") is None:
        raise ServiceError("systemctl not found")
    result = subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60
    )
    if check and result.returncode != 0:
        raise ServiceError((result.stderr or result.stdout).strip() or f"systemctl {args[0]} failed")
    return result


def is_enabled() -> bool:
    return _systemctl("is-enabled", UNIT_NAME, check=False).returncode == 0


def is_active() -> bool:
    return _systemctl("is-active", UNIT_NAME, check=False).returncode == 0


def install_unit() -> None:
    """Render the unit template (pointing at this checkout) into the user's
    systemd directory, reloading systemd only if the file changed."""
    rendered = UNIT_TEMPLATE.read_text().replace("@REPO@", str(REPO_DIR))
    target = USER_UNIT_DIR / UNIT_NAME
    if target.exists() and target.read_text() == rendered:
        return
    USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered)
    _systemctl("daemon-reload")


def enable() -> None:
    install_unit()
    _systemctl("enable", "--now", UNIT_NAME)


def disable() -> None:
    _systemctl("disable", "--now", UNIT_NAME)
