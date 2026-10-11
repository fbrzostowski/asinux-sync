import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asinux_sync.cloud import rclone  # noqa: E402


def test_write_remote_preserves_other_remotes(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[other]\ntype = s3\nsecret = a%b\n")
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))

    token = json.dumps({"access_token": "t", "refresh_token": "r"})
    rclone.write_remote("asinux-cloud", {"type": "onedrive", "token": token, "drive_id": "1", "drive_type": "personal"})

    text = conf.read_text()
    assert "[other]" in text and "secret = a%b" in text
    assert "drive_id = 1" in text
    assert oct(conf.stat().st_mode & 0o777) == "0o600"


@pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")
def test_rclone_reads_written_remote(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    monkeypatch.setenv("RCLONE_CONFIG", str(conf))
    rclone.write_remote("x", {"type": "onedrive", "token": '{"access_token":"t"}', "drive_id": "1", "drive_type": "personal"})
    cfg = rclone.remote_config("x")
    assert cfg["drive_id"] == "1" and cfg["type"] == "onedrive"
    assert rclone.RcloneBackend.is_configured("x")
