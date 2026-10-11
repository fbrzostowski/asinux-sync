"""Helpers for modules that ship files (wallpapers, videos, themes) as assets.

Assets are content-addressed (`<module>-<sha256[:16]>-<name>`) so identical
files are uploaded once. Everything here is deterministic on purpose: the
background service captures every minute, and a capture that produced a
different asset name each time would look like a local change and be pushed
forever. Two further tricks keep the per-minute cost low: a size+mtime cache
avoids re-hashing big files, and tar bundles use fixed metadata.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import re
import shutil
import tarfile
from pathlib import Path

from .. import config as config_module

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_BUNDLE_BYTES = 200 * 1024 * 1024

# A bundle member: a regular file on disk, literal bytes, or ("symlink", target).
Member = Path | bytes | tuple


def assets_home() -> Path:
    return Path.home() / ".local" / "share" / "asinux-sync" / "assets"


def is_system_path(path: Path) -> bool:
    """Shipped by the distribution - every ASinux machine already has it."""
    return str(path).startswith(("/usr/", "/etc/", "/opt/"))


def strip_prefix(module_id: str, name: str) -> str:
    """Remove a `<module_id>-<digest>-` prefix, so a file restored from an
    asset isn't re-prefixed (and renamed) every capture/apply cycle."""
    match = re.match(rf"^{re.escape(module_id)}-[0-9a-f]{{16}}-(.+)$", name)
    return match.group(1) if match else name


# -- cache -------------------------------------------------------------------

def _cache_path() -> Path:
    return config_module.STATE_DIR / "asset-cache.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))


# -- single files ----------------------------------------------------------------

def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_file(module_id: str, path: Path, assets_dir: Path) -> str | None:
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    if stat.st_size > MAX_FILE_BYTES:
        logger.warning("%s: %s is larger than %d MB, not syncing it", module_id, path, MAX_FILE_BYTES >> 20)
        return None

    cache = _load_cache()
    key = f"file:{module_id}:{path}"
    entry = cache.get(key)
    if (
        entry
        and entry["size"] == stat.st_size
        and entry["mtime_ns"] == stat.st_mtime_ns
        and (assets_dir / entry["asset"]).is_file()
    ):
        return entry["asset"]

    name = f"{module_id}-{_file_digest(path)[:16]}-{strip_prefix(module_id, path.name)}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = assets_dir / name
    if not dest.exists():
        shutil.copyfile(path, dest)
    cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "asset": name}
    _save_cache(cache)
    return name


def restore_file(asset_name: str, assets_dir: Path) -> Path | None:
    """Copy a downloaded asset to a stable local path and return it."""
    source = assets_dir / asset_name
    if not source.is_file():
        return None
    target_dir = assets_home()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / asset_name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)
    return target


# -- bundles (deterministic tar.gz) ----------------------------------------------------

def tree_members(directory: Path) -> dict[str, Member]:
    """Every file and symlink under `directory`, keyed by `<dirname>/<rel>`."""
    members: dict[str, Member] = {}
    directory = Path(directory)
    for path in sorted(directory.rglob("*")):
        arcname = f"{directory.name}/{path.relative_to(directory).as_posix()}"
        if path.is_symlink():
            members[arcname] = ("symlink", str(path.readlink()))
        elif path.is_file():
            members[arcname] = path
    return members


def _fingerprint(members: dict[str, Member]) -> tuple[str, int] | None:
    digest = hashlib.sha256()
    total = 0
    for arcname in sorted(members):
        member = members[arcname]
        if isinstance(member, bytes):
            digest.update(f"{arcname}\0b\0".encode() + hashlib.sha256(member).digest())
            total += len(member)
        elif isinstance(member, tuple):
            digest.update(f"{arcname}\0l\0{member[1]}".encode())
        else:
            try:
                stat = member.stat()
            except OSError:
                return None
            digest.update(f"{arcname}\0f\0{stat.st_size}\0{stat.st_mtime_ns}".encode())
            total += stat.st_size
    return digest.hexdigest(), total


def store_bundle(
    module_id: str, label: str, members: dict[str, Member], assets_dir: Path, cache_key: str | None = None
) -> str | None:
    """`label` ends up in the asset name, so it must not depend on where the
    sources live; `cache_key` (default: the label) says which sources the
    size/mtime cache entry belongs to."""
    fingerprint = _fingerprint(members)
    if fingerprint is None:
        return None
    fingerprint_hex, total = fingerprint
    if total > MAX_BUNDLE_BYTES:
        logger.warning("%s: %s is larger than %d MB, not syncing it", module_id, label, MAX_BUNDLE_BYTES >> 20)
        return None

    cache = _load_cache()
    key = f"bundle:{module_id}:{cache_key or label}"
    entry = cache.get(key)
    if entry and entry["fingerprint"] == fingerprint_hex and (assets_dir / entry["asset"]).is_file():
        return entry["asset"]

    assets_dir.mkdir(parents=True, exist_ok=True)
    temp = assets_dir / f".{module_id}-{label}.tmp"
    with open(temp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for arcname in sorted(members):
                    _add_member(tar, arcname, members[arcname])
    digest = _file_digest(temp)[:16]
    name = f"{module_id}-{digest}-{label}.tar.gz"
    final = assets_dir / name
    if final.exists():
        temp.unlink()
    else:
        temp.replace(final)
    cache[key] = {"fingerprint": fingerprint_hex, "asset": name}
    _save_cache(cache)
    return name


def _add_member(tar: tarfile.TarFile, arcname: str, member: Member) -> None:
    info = tarfile.TarInfo(arcname)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if isinstance(member, tuple):
        info.type = tarfile.SYMTYPE
        info.linkname = member[1]
        tar.addfile(info)
    elif isinstance(member, bytes):
        info.size = len(member)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(member))
    else:
        info.size = member.stat().st_size
        info.mode = 0o644
        with open(member, "rb") as handle:
            tar.addfile(info, handle)


def extract_bundle(asset_name: str, assets_dir: Path, destination: Path) -> bool:
    """Extract a bundle, skipping (not aborting on) members the safe 'data'
    filter refuses - absolute symlinks, paths escaping the destination."""
    source = assets_dir / asset_name
    if not source.is_file():
        return False
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source, "r:gz") as tar:
        for member in tar:
            try:
                tar.extract(member, destination, filter="data")
            except (tarfile.FilterError, OSError) as exc:
                logger.warning("skipping %s while extracting %s: %s", member.name, asset_name, exc)
    return True
