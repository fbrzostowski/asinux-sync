"""Thin subprocess wrapper around the `rclone` CLI.

asinux-sync never talks to OneDrive/Nextcloud/Google APIs directly - every
provider is just an rclone remote. Adding a new cloud provider later means
adding a new rclone remote type, not touching this module or the sync engine.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


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
        on_process: Callable[[subprocess.Popen], None] | None = None,
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
        if on_process is not None:
            on_process(proc)
        # The read loop below blocks until rclone exits, so the timeout has
        # to be enforced from a timer that kills the process.
        timed_out = threading.Event()

        def expire():
            timed_out.set()
            proc.kill()

        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        lines: list[str] = []
        url_reported = False
        try:
            for line in proc.stdout:
                lines.append(line)
                if not url_reported and "/auth?" in line:
                    # Not just any URL: rclone also prints the redirect URL
                    # (http://localhost:53682/) in an earlier notice line.
                    for token in line.split():
                        if token.startswith("http") and "/auth?" in token:
                            on_auth_url(token.strip())
                            url_reported = True
                            break
            proc.wait()
        finally:
            watchdog.cancel()

        if timed_out.is_set():
            raise RcloneError("Sign-in timed out")
        output = "".join(lines)
        if proc.returncode != 0:
            if "address already in use" in output:
                raise RcloneError("Another sign-in is already in progress - close it and try again")
            errors = [l.strip() for l in lines if l.strip().startswith("Error:")]
            raise RcloneError(errors[-1] if errors else f"rclone authorize failed (exit {proc.returncode})")

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
        params = dict(extra_params or {})
        if provider_type == "onedrive" and "drive_id" not in params:
            params.update(onedrive_drive_params(token_json))
        write_remote(remote_name, {"type": provider_type, "token": token_json, **params})
        return cls(remote_name)

    @classmethod
    def repair(cls, remote_name: str) -> bool:
        """Finish a OneDrive remote that has a token but no drive_id/drive_type
        (left behind when `rclone config create` stalled on its drive
        question). Returns True if something was repaired."""
        cfg = remote_config(remote_name)
        if cfg is None or cfg.get("type") != "onedrive" or cfg.get("drive_id"):
            return False
        write_remote(remote_name, {**cfg, **onedrive_drive_params(cfg["token"])})
        return True

    @classmethod
    def is_configured(cls, remote_name: str) -> bool:
        cfg = remote_config(remote_name)
        if cfg is None:
            return False
        return cfg.get("type") != "onedrive" or bool(cfg.get("drive_id"))

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


def _config_path() -> Path:
    override = os.environ.get("RCLONE_CONFIG")
    return Path(override) if override else Path.home() / ".config" / "rclone" / "rclone.conf"


def write_remote(remote_name: str, params: dict[str, str]) -> None:
    """Write/replace one [remote] section in rclone.conf directly.

    `rclone config create/update` can't be used for this: for OAuth backends
    they re-enter the interactive authorization flow and block waiting for a
    browser, even with stdin closed. rclone.conf is a plain INI file, and
    other remotes in it are preserved.
    """
    path = _config_path()
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # rclone keys are case-sensitive
    if path.exists():
        parser.read(path, encoding="utf-8")
    parser[remote_name] = dict(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        parser.write(handle)
    os.replace(tmp, path)


def remote_config(remote_name: str) -> dict | None:
    result = _run(["rclone", "config", "dump"], check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout or "{}").get(remote_name)


def onedrive_drive_params(token_json: str) -> dict[str, str]:
    """Ask Microsoft Graph for the account's drive, so rclone doesn't need to
    ask (it prompts interactively, which hangs without a terminal)."""
    access_token = json.loads(token_json)["access_token"]
    request = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/drive?$select=id,driveType",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            drive = json.load(response)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise RcloneError(f"Couldn't read your OneDrive details: {exc}") from exc
    return {"drive_id": drive["id"], "drive_type": drive["driveType"]}


def _run(args: Iterable[str], *, text: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    # stdin is closed so rclone can never block on an interactive prompt.
    try:
        proc = subprocess.run(list(args), capture_output=True, text=text, stdin=subprocess.DEVNULL, timeout=600)
    except FileNotFoundError as exc:
        raise RcloneError("rclone is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RcloneError("rclone took too long to respond") from exc
    if check and proc.returncode != 0:
        stderr = proc.stderr if text else proc.stderr.decode(errors="replace")
        raise RcloneError(f"{' '.join(args)} failed: {stderr}")
    return proc
