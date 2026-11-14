"""Request kind "theme": fetch themes this computer lacks from a device that has them.

A theme that lives in the user's home is uploaded with the profile already
(modules/appearance.py). One installed system-wide (/usr/share/icons,
/usr/share/themes - e.g. from a package) is not, since most computers have
it too. When one doesn't, it is asked for on the request board
(device_requests.py):

1. Computer A applies the profile, finds that it lacks icon theme "Papirus"
   (or a theme it inherits from) and asks for it.
2. Computer B, on its next cycle, finds Papirus in ~/.icons,
   ~/.local/share/icons or /usr/share/icons and sends that folder.
3. Computer A puts it into ~/.local/share/icons and applies the cloud's icon
   theme setting on its next cycle.

Covers everything modules/appearance.py THEME_ITEMS lists: app (GTK) theme,
icons, cursor and Shell theme.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from .device_requests import RequestHandler
from .modules import appearance

logger = logging.getLogger(__name__)

MAX_TRANSFER_BYTES = 1024 * 1024 * 1024


# -- packing a theme folder ----------------------------------------------------------

def _members(folder: Path) -> list[tuple[str, Path | str]]:
    """(arcname, file) for files, (arcname, "->target") for symlinks.

    Links that stay inside the folder are kept as links. Ones that point
    outside it (common under /usr/share/icons) would be refused on extraction
    or dangle on the other computer, so the file they point at is sent
    instead; a link to something that isn't a file is left out."""
    root = folder.resolve()
    members: list[tuple[str, Path | str]] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if rel == appearance.TRANSFER_MARKER:
            continue
        arcname = f"{folder.name}/{rel}"
        if path.is_symlink():
            target = os.readlink(path)
            resolved = path.resolve()
            if not os.path.isabs(target) and resolved.is_relative_to(root):
                members.append((arcname, "->" + target))
            elif resolved.is_file():
                members.append((arcname, resolved))
        elif path.is_file():
            members.append((arcname, path))
    return members


def pack(folder: Path, destination: Path) -> str | None:
    """Write `folder` to `destination` as tar.gz. Returns why it couldn't, or None."""
    members = _members(folder)
    total = sum(m.stat().st_size for _a, m in members if isinstance(m, Path))
    if total > MAX_TRANSFER_BYTES:
        return f"{folder.name} is larger than {MAX_TRANSFER_BYTES >> 20} MB"
    with tarfile.open(destination, "w:gz") as tar:
        for arcname, member in members:
            if isinstance(member, str):
                info = tarfile.TarInfo(arcname)
                info.type = tarfile.SYMTYPE
                info.linkname = member[2:]
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(arcname)
                info.size = member.stat().st_size
                info.mode = 0o644
                info.mtime = int(member.stat().st_mtime)
                with open(member, "rb") as handle:
                    tar.addfile(info, handle)
    return None


def unpack(archive: Path, name: str, destination: Path) -> bool:
    """Extract the theme folder `name` from `archive` into `destination`.
    Only that one folder is taken, whatever else the archive holds."""
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    staging = Path(tempfile.mkdtemp(prefix=".asinux-sync-", dir=destination))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if member.name.split("/", 1)[0] != name:
                    continue
                try:
                    tar.extract(member, staging, filter="data")
                except (tarfile.FilterError, OSError) as exc:
                    logger.warning("skipping %s of %s: %s", member.name, name, exc)
        if not (staging / name).is_dir():
            return False
        (staging / name / appearance.TRANSFER_MARKER).write_text(
            "Fetched by Asinux Sync from another of your devices.\n")
        if target.exists():
            return target.is_dir()  # appeared meanwhile (installed by hand): keep that one
        (staging / name).replace(target)
        return True
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# -- the handler ----------------------------------------------------------------------------

class ThemeHandler(RequestHandler):
    """data: {"item": <appearance item id>, "name": <theme folder name>}."""

    kind = "theme"

    def wanted(self, ctx) -> dict[str, dict]:
        profile = ctx.engine.last_remote
        disabled = set(ctx.cfg.disabled_modules)
        if not profile or "appearance" in disabled:
            return {}
        items = profile.get("modules", {}).get("appearance", {}).get("items", {})
        wanted = {}
        for item_id in appearance.THEME_ITEMS:
            data = items.get(item_id)
            if not data or f"appearance.{item_id}" in disabled:
                continue
            shipped = (data.get("theme") or {}).get("name")
            for name in appearance.missing(item_id, appearance.wanted_name(item_id, data)):
                if name != shipped:  # that one comes with the profile
                    wanted[f"{item_id}/{name}"] = {"item": item_id, "name": name}
        return wanted

    @staticmethod
    def _valid(data: dict) -> bool:
        return data.get("item") in appearance.THEME_ITEMS and bool(data.get("name"))

    def satisfied(self, data: dict, ctx) -> bool:
        return self._valid(data) and appearance.locate(data["item"], data["name"]) is not None

    def can_send(self, data: dict, ctx) -> bool:
        return self.satisfied(data, ctx)

    def pack(self, data: dict, destination: Path, ctx) -> str | None:
        return pack(appearance.locate(data["item"], data["name"]), destination)

    def receive(self, data: dict, archive: Path, ctx) -> bool:
        return self._valid(data) and unpack(archive, data["name"], appearance.install_dir(data["item"]))

    def done(self, data: dict, ctx) -> None:
        # Its setting was held back while the theme was missing: apply it next cycle.
        ctx.engine.forget_items([f"appearance.{data['item']}"])
