"""Thin subprocess wrapper around the `rclone` CLI.

asinux-sync never talks to OneDrive/Nextcloud/Google APIs directly - every
provider is just an rclone remote. Adding a new cloud provider later means
adding a new rclone remote type, not touching this module or the sync engine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass


class RcloneError(RuntimeError):
    pass


def is_installed() -> bool:
    return shutil.which("rclone") is not None


@dataclass
class AuthorizeResult:
    """Result of an interactive `rclone authorize` flow."""

    token_json: str
    """The OAuth token blob rclone printed at the end - feed this straight
    into RcloneBackend.config_create(..., token=...)."""


class RcloneBackend:
    """One rclone remote (one connected cloud account)."""

    def __init__(self, remote_name: str):
        self.remote_name = remote_name

    # -- connecting -----------------------------------------------------

    @staticmethod
    def authorize(
        provider_type: str,
        on_auth_url: Callable[[str], None],
        timeout: float = 300,
    ) -> AuthorizeResult:
        """Run `rclone authorize <provider_type>` and drive an interactive
        OAuth flow without needing a desktop-wide browser/account
        integration (GNOME Online Accounts etc).

        rclone prints the URL to open on one of the first lines of stdout,
        then blocks until the local redirect handler receives the OAuth
        callback, then prints a single JSON token blob as the last line of
        stdout. `on_auth_url` is called as soon as the URL is known so a
        caller (e.g. a GTK dialog) can display it - the URL can be opened
        in any browser on the same machine, including a private window, so
        this works even when invoked from a different user's desktop
        session than the one the cloud account belongs to.
        """
        try:
            proc = subprocess.Popen(
                ["rclone", "authorize", provider_type],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RcloneError("rclone is not installed") from exc
        assert proc.stdout is not None
        lines: list[str] = []
        url_reported = False
        try:
            for line in proc.stdout:
                lines.append(line)
                if not url_reported and ("http://" in line or "https://" in line):
                    for token in line.split():
                        if token.startswith("http://") or token.startswith("https://"):
                            on_auth_url(token.strip())
                            url_reported = True
                            break
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise RcloneError("rclone authorize timed out waiting for login") from exc

        if proc.returncode != 0:
            raise RcloneError(f"rclone authorize failed:\n{''.join(lines)}")

        token_line = next((l for l in reversed(lines) if l.strip().startswith("{")), None)
        if token_line is None:
            raise RcloneError(f"rclone authorize produced no token:\n{''.join(lines)}")
        return AuthorizeResult(token_json=token_line.strip())

    @classmethod
    def config_create(
        cls,
        remote_name: str,
        provider_type: str,
        token_json: str,
        extra_params: dict[str, str] | None = None,
    ) -> "RcloneBackend":
        args = [
            "rclone",
            "config",
            "create",
            remote_name,
            provider_type,
            "token",
            token_json,
        ]
        for key, value in (extra_params or {}).items():
            args.append(key)
            args.append(value)
        _run(args)
        return cls(remote_name)

    @classmethod
    def exists(cls, remote_name: str) -> bool:
        result = _run(["rclone", "listremotes"])
        return f"{remote_name}:" in result.stdout.splitlines()

    # -- transfer ---------------------------------------------------------

    def _path(self, remote_path: str) -> str:
        return f"{self.remote_name}:{remote_path.lstrip('/')}"

    def sync(self, local_dir: str, remote_path: str, *, direction: str = "push") -> None:
        """direction='push' copies local_dir -> remote, 'pull' the reverse.
        Uses `sync` (mirrors deletions) rather than `copy` deliberately -
        profile.json/assets are a single owned tree, not a general file drop.
        """
        src, dst = (local_dir, self._path(remote_path)) if direction == "push" else (self._path(remote_path), local_dir)
        _run(["rclone", "sync", src, dst])

    def copyto(self, local_file: str, remote_path: str) -> None:
        _run(["rclone", "copyto", local_file, self._path(remote_path)])

    def cat(self, remote_path: str) -> bytes:
        result = _run(["rclone", "cat", self._path(remote_path)], text=False)
        return result.stdout

    def rcat(self, remote_path: str, data: bytes) -> None:
        """Write `data` directly to a remote file without a local temp file."""
        try:
            proc = subprocess.run(
                ["rclone", "rcat", self._path(remote_path)],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RcloneError("rclone is not installed") from exc
        if proc.returncode != 0:
            raise RcloneError(f"rclone rcat failed: {proc.stderr.decode(errors='replace')}")

    def lsjson(self, remote_path: str = "") -> list[dict]:
        result = _run(["rclone", "lsjson", self._path(remote_path)], check=False)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout)

    def exists_dir(self, remote_path: str) -> bool:
        return _run(["rclone", "lsf", self._path(remote_path)], check=False).returncode == 0

    def exists_file(self, remote_path: str) -> bool:
        result = _run(["rclone", "lsf", self._path(remote_path)], check=False)
        return result.returncode == 0 and result.stdout.strip() != ""


def _run(args: Iterable[str], *, text: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(list(args), capture_output=True, text=text)
    except FileNotFoundError as exc:
        raise RcloneError("rclone is not installed") from exc
    if check and proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode(errors="replace")
        raise RcloneError(f"{' '.join(args)} failed: {stderr}")
    return proc
