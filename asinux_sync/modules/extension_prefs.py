"""Read and write one GNOME Shell extension's settings (dconf folders)."""

from __future__ import annotations

import re
import subprocess

from . import shell_extensions
from .base import ModuleUnavailable

_SCHEMA_PATH = re.compile(r'<schema\b[^>]*?\bpath="([^"]+)"')
# Never reach above this: a schema path must name a folder of its own.
_EXTENSIONS_ROOT = "/org/gnome/shell/extensions/"


def convention_path(uuid: str) -> str:
    # What asinux-layouts assumes: /org/gnome/shell/extensions/<name>/
    return f"{_EXTENSIONS_ROOT}{uuid.split('@')[0]}/"


def paths(uuid: str) -> list[str]:
    """The dconf folders this extension really uses here.

    Read from the schemas it ships: many extensions keep their settings
    somewhere other than the convention (ddterm, Just Perfection, AppIndicator,
    the Compiz effects...). Falls back to the convention when the extension
    ships no readable schema."""
    found: list[str] = []
    folder = shell_extensions.extension_dir(uuid)
    if folder is not None:
        for schema_file in sorted((folder / "schemas").glob("*.gschema.xml")):
            try:
                text = schema_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in _SCHEMA_PATH.finditer(text):
                path = match.group(1) if match.group(1).endswith("/") else match.group(1) + "/"
                if path.count("/") >= 4 and not _EXTENSIONS_ROOT.startswith(path) and path not in found:
                    found.append(path)
    # A dump of a folder includes its subfolders: keep only the outermost ones.
    found = [p for p in found if not any(p != other and p.startswith(other) for other in found)]
    return found or [convention_path(uuid)]


def _dconf(*args: str, stdin: str | None = None) -> str:
    try:
        result = subprocess.run(["dconf", *args], input=stdin, capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        raise ModuleUnavailable("the dconf command is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ModuleUnavailable("dconf did not answer") from exc
    if result.returncode != 0:
        raise ModuleUnavailable(result.stderr.strip() or f"dconf {args[0]} failed")
    return result.stdout


def dump(uuid: str) -> dict[str, str]:
    """{dconf folder: its settings} for every folder that has any."""
    settings = {}
    for path in paths(uuid):
        text = _dconf("dump", path)
        if text.strip():
            settings[path] = text
    return settings


def apply(uuid: str, wanted: dict[str, str] | str) -> None:
    """Make the extension's folders exactly `wanted`; untouched if they already are.

    Only folders this machine's copy of the extension declares are written -
    a profile can't make us write anywhere else in dconf."""
    if isinstance(wanted, str):  # the single-folder format of earlier versions
        wanted = {convention_path(uuid): wanted}
    allowed = set(paths(uuid))
    for path, text in wanted.items():
        if path not in allowed or not text.strip() or _dconf("dump", path) == text:
            continue
        _dconf("reset", "-f", path)
        _dconf("load", path, stdin=text)
