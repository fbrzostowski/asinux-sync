"""Default shell and the shell prompt: Tide for fish, Powerline for zsh.

Same idea of "default shell" as /opt/asinux-system-configuration/
shell_settings.py: the login shell, one of sh, bash, zsh and fish.

Changing the login shell or installing a package needs the administrator
password, and Tide is downloaded from GitHub, so apply_item never does
either: the background service asks first (shell_setup.py and
asinux_sync_settings/shell_prompt.py). What apply_item does on its own is
only what needs nothing installed - Tide's settings when Tide is here, and
the Powerline lines in ~/.zshrc when the powerline package is here.
"""

from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

from .base import Item, SyncModule

logger = logging.getLogger(__name__)

SHELLS = ("sh", "bash", "zsh", "fish")
POWERLINE_ZSH = Path("/usr/share/powerline/zsh/powerline.zsh")
BLOCK_START = "# >>> asinux-sync: powerline >>>"
BLOCK_END = "# <<< asinux-sync: powerline <<<"
BLOCK = f"""{BLOCK_START}
if [ -f {POWERLINE_ZSH} ]; then
  source {POWERLINE_ZSH}
fi
{BLOCK_END}
"""

# Reads every tide_* universal variable as name \x1d count \x1d values (\x1f-separated) \x1e.
_READ_TIDE = (
    'for n in (set --names -U | string match -- "tide_*"); '
    'printf "%s\\x1d%d\\x1d" $n (count $$n); printf "%s\\x1f" $$n; printf "\\x1e"; end'
)


# -- default shell ---------------------------------------------------------------------------

def current_shell() -> str:
    try:
        return os.path.basename(pwd.getpwuid(os.getuid()).pw_shell)
    except KeyError:
        return ""


def allowed_shells() -> set[str]:
    try:
        with open("/etc/shells", encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


def shell_path(name: str) -> str | None:
    """The installed, /etc/shells-listed path of shell `name`, if any."""
    allowed = allowed_shells()
    for directory in ("/usr/bin", "/bin"):
        path = os.path.join(directory, name)
        if os.path.exists(path) and (not allowed or path in allowed):
            return path
    return None


# -- Tide (fish) -------------------------------------------------------------------------------

def fish_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fish"


def tide_installed() -> bool:
    return (fish_config_dir() / "functions" / "tide.fish").is_file()


def _fish(*args: str) -> subprocess.CompletedProcess | None:
    if shutil.which("fish") is None:
        return None
    try:
        return subprocess.run(["fish", "-c", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("shell: running fish failed: %s", exc)
        return None


def read_tide_settings() -> dict[str, list[str]]:
    result = _fish(_READ_TIDE)
    if result is None or result.returncode != 0:
        return {}
    settings = {}
    for record in result.stdout.split("\x1e"):
        name, sep, rest = record.partition("\x1d")
        count, sep2, values = rest.partition("\x1d")
        if not (sep and sep2 and name and count.isdigit()):
            continue
        settings[name] = values.split("\x1f")[: int(count)]
    return settings


def write_tide_settings(wanted: dict[str, list[str]]) -> None:
    """Set the tide_* variables that differ. Ones the cloud doesn't name are
    left alone (a newer Tide here may have settings an older one lacks)."""
    current = read_tide_settings()
    for name, values in sorted(wanted.items()):
        if not name.startswith("tide_") or current.get(name) == values:
            continue
        result = _fish("set -U $argv[1] $argv[2..-1]", name, *values)
        if result is None or result.returncode != 0:
            logger.warning("shell: couldn't set %s", name)


# -- Powerline (zsh) ---------------------------------------------------------------------------

def zshrc_path() -> Path:
    return Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"


def _read_zshrc() -> str:
    try:
        return zshrc_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def powerline_enabled() -> bool:
    """Our block is in ~/.zshrc, or a line of the user's own loads Powerline."""
    for line in _read_zshrc().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "powerline" in stripped and "zsh" in stripped:
            return True
    return False


def set_powerline(enabled: bool) -> None:
    text = _read_zshrc()
    without = re.sub(rf"\n?{re.escape(BLOCK_START)}.*?{re.escape(BLOCK_END)}\n?", "\n", text, flags=re.S)
    if enabled:
        if powerline_enabled():
            return
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + BLOCK
    else:
        if without == text:
            return  # only ever removes our own lines
        new = without.strip("\n") + "\n" if without.strip() else ""
    path = zshrc_path()
    temp = path.with_name(path.name + ".asinux-sync.tmp")
    temp.write_text(new, encoding="utf-8")
    temp.replace(path)


# -- the module -----------------------------------------------------------------------------------

class ShellModule(SyncModule):
    module_id = "shell"
    display_name = "Shell"
    description = "Default shell and the prompt: Tide for fish, Powerline for zsh"
    category = "System"

    def list_items(self) -> list[Item]:
        return [
            Item("default_shell", "Default shell", "The shell a new terminal starts (asks for your password to change)"),
            Item("tide", "Tide prompt (fish)", "Whether Tide is installed, and its settings"),
            Item("powerline", "Powerline prompt (zsh)", "Whether zsh uses the Powerline prompt"),
        ]

    def is_default(self, item_id: str, data: dict) -> bool:
        if item_id == "default_shell":
            return data.get("shell") in ("", "bash")  # what a new account gets
        if item_id == "tide":
            return not data.get("installed")
        return not data.get("enabled")

    def capture_item(self, item_id: str, assets_dir: Path) -> dict | None:
        if item_id == "default_shell":
            return {"shell": current_shell()}
        if item_id == "tide":
            installed = tide_installed()
            return {"installed": installed, "settings": read_tide_settings() if installed else {}}
        if item_id == "powerline":
            return {"enabled": powerline_enabled()}
        return None

    def apply_item(self, item_id: str, data: dict, assets_dir: Path) -> None:
        if item_id == "tide" and data.get("installed") and tide_installed():
            write_tide_settings(data.get("settings", {}))
        elif item_id == "powerline":
            if not data.get("enabled"):
                set_powerline(False)
            elif POWERLINE_ZSH.is_file():
                set_powerline(True)
        # default_shell, and installing Tide or Powerline: shell_setup.py asks first.
