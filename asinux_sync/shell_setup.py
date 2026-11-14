"""What the shell part of the profile needs that can't be done silently.

Switching the login shell and installing packages need the administrator
password (pkexec); Tide is downloaded from GitHub. So the background service
asks first, in a window of its own (asinux_sync_settings/shell_prompt.py),
the way it does for missing extensions. What the user declines is
remembered in Config.declined_shell_setup and not asked about again here.

A step is {"id", "title", "subtitle", "packages", and "shell" or "tide"}:
  shell:<name>  make <name> the login shell (installing it first if needed)
  tide          install Fisher and Tide for fish (and fish if needed)
  powerline     install the powerline package (and zsh if needed); the
                module then adds it to ~/.zshrc
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Collection, Iterable

from .modules import shell

TIDE = "IlanCosman/tide@v6"
FISHER_URL = "https://raw.githubusercontent.com/jorgebucaran/fisher/main/functions/fisher.fish"
PACKAGES = {"sh", "bash", "zsh", "fish", "powerline"}  # nothing else is ever installed

# One password prompt for everything that needs root: install, then switch.
_ADMIN_SCRIPT = 'set -e\n[ -z "$1" ] || dnf install -y $1\n[ -z "$2" ] || usermod -s "$2" -- "$3"\n'
_INSTALL_TIDE = (
    f"functions -q fisher; or begin; curl -sL {FISHER_URL} | source; and fisher install jorgebucaran/fisher; end; "
    f"and fisher install {TIDE}"
)


def find_needed(profile: dict | None, disabled: Collection[str], declined: Collection[str]) -> list[dict]:
    if not profile or profile.get("version", 1) < 2 or "shell" in disabled:
        return []
    items = profile.get("modules", {}).get("shell", {}).get("items", {})
    steps = []

    wanted = (items.get("default_shell") or {}).get("shell")
    if (
        wanted in shell.SHELLS
        and "shell.default_shell" not in disabled
        and wanted != shell.current_shell()
        and f"shell:{wanted}" not in declined
    ):
        steps.append({
            "id": f"shell:{wanted}",
            "title": f"Make {wanted} your default shell",
            "subtitle": "Takes effect in new terminals" + ("" if shell.shell_path(wanted) else f" - installs {wanted}"),
            "packages": [] if shell.shell_path(wanted) else [wanted],
            "shell": wanted,
        })

    tide = items.get("tide") or {}
    if tide.get("installed") and "shell.tide" not in disabled and not shell.tide_installed() and "tide" not in declined:
        fish_missing = shutil.which("fish") is None
        steps.append({
            "id": "tide",
            "title": "Install the Tide prompt for fish",
            "subtitle": "Downloaded from GitHub with Fisher" + (" - installs fish" if fish_missing else ""),
            "packages": ["fish"] if fish_missing else [],
            "tide": True,
        })

    powerline = items.get("powerline") or {}
    if (
        powerline.get("enabled")
        and "shell.powerline" not in disabled
        and not shell.POWERLINE_ZSH.is_file()
        and "powerline" not in declined
    ):
        packages = ["powerline"] + ([] if shutil.which("zsh") else ["zsh"])
        steps.append({
            "id": "powerline",
            "title": "Install the Powerline prompt for zsh",
            "subtitle": "Installs " + " and ".join(packages),
            "packages": packages,
        })
    return steps


def admin_command(steps: Iterable[dict], user: str) -> list[str] | None:
    """The one pkexec call the chosen steps need, or None if none needs root."""
    steps = list(steps)
    packages = sorted({p for s in steps for p in s.get("packages", []) if p in PACKAGES})
    target = next((s["shell"] for s in steps if s.get("shell") in shell.SHELLS), None)
    if not packages and target is None:
        return None
    path = (shell.shell_path(target) or f"/usr/bin/{target}") if target else ""
    return ["pkexec", "/bin/sh", "-c", _ADMIN_SCRIPT, "sh", " ".join(packages), path, user]


def run(steps: list[dict], user: str, runner=subprocess.run) -> dict[str, str]:
    """Carry out the chosen steps. Returns id -> "done", "cancelled" or "error: ..."."""
    outcomes: dict[str, str] = {}
    command = admin_command(steps, user)
    admin_ok = True
    if command is not None:
        try:
            result = runner(command, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = None
            reason = f"error: {exc}"
        else:
            if result.returncode in (126, 127):  # pkexec: dialog dismissed / not authorized
                reason = "cancelled"
            else:
                reason = f"error: {(result.stderr or result.stdout).strip()[-300:] or 'failed'}"
        admin_ok = result is not None and result.returncode == 0
        for step in steps:
            if step.get("packages") or step.get("shell"):
                outcomes[step["id"]] = "done" if admin_ok else reason

    for step in steps:
        if not step.get("tide") or outcomes.get(step["id"], "done") != "done":
            continue
        try:
            result = runner(["fish", "-c", _INSTALL_TIDE], capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            outcomes[step["id"]] = f"error: {exc}"
            continue
        ok = result.returncode == 0 and shell.tide_installed()
        outcomes[step["id"]] = "done" if ok else f"error: {(result.stderr or result.stdout).strip()[-300:] or 'failed'}"

    for step in steps:
        outcomes.setdefault(step["id"], "done")
    return outcomes


def build_result(requested: Iterable[str], selected: Collection[str], outcomes: dict[str, str]) -> dict:
    """done: carried out; declined: unchecked, cancelled or failed (not asked
    about again here); failed: why the ones that were wanted failed."""
    done, declined, failed = [], [], {}
    for step_id in requested:
        if step_id not in selected:
            declined.append(step_id)
            continue
        outcome = outcomes.get(step_id, "error: not attempted")
        if outcome == "done":
            done.append(step_id)
        else:
            declined.append(step_id)
            if outcome != "cancelled":
                failed[step_id] = outcome
    return {"done": done, "declined": declined, "failed": failed}
