"""Request kind "wallpaper": fetch system wallpapers this computer lacks.

A wallpaper that lives in the user's home is uploaded with the profile
(modules/background.py). One that ships with the system (/usr/share/
backgrounds/...) is only referenced. When a computer doesn't have it, it is
asked for on the request board (device_requests.py):

1. Computer A applies the profile, finds that the desktop or lock screen
   picture is a system file it lacks, keeps its own picture and asks for it.
2. Computer B has the file and sends it - for a dynamic wallpaper (.xml)
   together with every picture the XML names.
3. Computer A unpacks it under ~/.local/share/asinux-sync/assets/system/
   (mirroring the system path, since /usr isn't writable) and applies the
   cloud's wallpaper on its next cycle; the setting points at the copy.

data: {"path": "/usr/share/backgrounds/..."}.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .device_requests import RequestHandler
from .modules import assets, background

logger = logging.getLogger(__name__)

MAX_TRANSFER_BYTES = 1024 * 1024 * 1024
_PATH_TAGS = ("file", "from", "to")


def _local(path: Path) -> Path | None:
    """This computer's copy of system file `path`: itself or a fetched one."""
    for candidate in (path, background.mirror(path)):
        if candidate.is_file():
            return candidate
    return None


def _referenced(xml_text: str) -> list[str]:
    """The picture paths a dynamic-wallpaper XML names, as written in it."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    found = []
    for element in root.iter():
        text = (element.text or "").strip()
        if element.tag in _PATH_TAGS and text and text not in found:
            found.append(text)
    return found


def _replace_path(xml_text: str, old: str, new: str) -> str:
    return re.sub(rf">\s*{re.escape(old)}\s*<", f">{new}<", xml_text)


def pack(path: Path, destination: Path) -> str | None:
    """Archive system file `path` (as it is here) under its system path.
    A dynamic wallpaper goes with its pictures, its XML naming them by their
    system paths again if this computer had fetched them itself."""
    source = _local(path)
    if source is None:
        return f"{path} is not here"
    members: dict[str, Path | bytes] = {}
    if path.suffix.lower() == ".xml":
        text = source.read_text(encoding="utf-8")
        for written in _referenced(text):
            picture = Path(written) if written.startswith("/") else path.parent / written
            original = background.unmirror(picture)
            local = _local(original)
            if local is None:
                continue
            if written.startswith("/") and str(original) != written:
                text = _replace_path(text, written, str(original))
            members[original.relative_to("/").as_posix()] = local
        members[path.relative_to("/").as_posix()] = text.encode("utf-8")
    else:
        members[path.relative_to("/").as_posix()] = source
    total = sum(m.stat().st_size if isinstance(m, Path) else len(m) for m in members.values())
    if total > MAX_TRANSFER_BYTES:
        return f"{path.name} is larger than {MAX_TRANSFER_BYTES >> 20} MB"
    with tarfile.open(destination, "w:gz") as tar:
        for arcname, member in members.items():
            if isinstance(member, bytes):
                info = tarfile.TarInfo(arcname)
                info.size = len(member)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(member))
            else:
                tar.add(member, arcname, recursive=False)
    return None


def unpack(archive: Path, path: Path) -> bool:
    """Put the files into the fetched-files folder, and point a dynamic
    wallpaper's XML at the pictures that are only there."""
    root = background.fetched_root()
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            try:
                tar.extract(member, root, filter="data")
            except (tarfile.FilterError, OSError) as exc:
                logger.warning("skipping %s: %s", member.name, exc)
    copy = background.mirror(path)
    if not copy.is_file():
        return False
    if path.suffix.lower() == ".xml":
        text = copy.read_text(encoding="utf-8")
        for written in _referenced(text):
            if written.startswith("/") and not Path(written).exists() and background.mirror(Path(written)).exists():
                text = _replace_path(text, written, str(background.mirror(Path(written))))
        copy.write_text(text, encoding="utf-8")
    return True


class WallpaperHandler(RequestHandler):
    kind = "wallpaper"

    def wanted(self, ctx) -> dict[str, dict]:
        profile = ctx.engine.last_remote
        disabled = set(ctx.cfg.disabled_modules)
        if not profile or "background" in disabled:
            return {}
        items = profile.get("modules", {}).get("background", {}).get("items", {})
        wanted = {}
        for item_id in background.PICTURES:
            if f"background.{item_id}" in disabled:
                continue
            for path in background.missing_system_files(items.get(item_id)):
                wanted[str(path)] = {"path": str(path)}
        return wanted

    @staticmethod
    def _path(data: dict) -> Path | None:
        raw = data.get("path", "")
        path = Path(raw)
        return path if raw and path.is_absolute() and assets.is_system_path(path) and ".." not in path.parts else None

    def satisfied(self, data: dict, ctx) -> bool:
        path = self._path(data)
        return path is not None and _local(path) is not None

    def can_send(self, data: dict, ctx) -> bool:
        return self.satisfied(data, ctx)

    def pack(self, data: dict, destination: Path, ctx) -> str | None:
        return pack(self._path(data), destination)

    def receive(self, data: dict, archive: Path, ctx) -> bool:
        path = self._path(data)
        return path is not None and unpack(archive, path)

    def done(self, data: dict, ctx) -> None:
        # The picture was left alone while the file was missing: apply it next cycle.
        ctx.engine.forget_items([f"background.{item_id}" for item_id in background.PICTURES])
