"""Default shell, Tide (fish) and Powerline (zsh)."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from asinux_sync import config as config_module
from asinux_sync import daemon, shell_setup
from asinux_sync.modules import shell
from asinux_sync.modules.shell import ShellModule


@pytest.fixture
def powerline_installed(tmp_path, monkeypatch):
    binding = tmp_path / "powerline.zsh"
    binding.write_text("# powerline\n")
    monkeypatch.setattr(shell, "POWERLINE_ZSH", binding)
    monkeypatch.setattr(shell, "BLOCK", shell.BLOCK.replace("/usr/share/powerline/zsh/powerline.zsh", str(binding)))
    return binding


def test_powerline_block_is_added_and_removed_leaving_the_rest(isolated_home, powerline_installed, monkeypatch):
    monkeypatch.delenv("ZDOTDIR", raising=False)
    zshrc = isolated_home / ".zshrc"
    zshrc.write_text("alias ll='ls -l'\n")
    module = ShellModule()
    assert module.capture_item("powerline", None) == {"enabled": False}

    module.apply_item("powerline", {"enabled": True}, None)
    assert module.capture_item("powerline", None) == {"enabled": True}
    assert zshrc.read_text().startswith("alias ll='ls -l'\n")
    module.apply_item("powerline", {"enabled": True}, None)  # once only
    assert zshrc.read_text().count(shell.BLOCK_START) == 1

    module.apply_item("powerline", {"enabled": False}, None)
    assert zshrc.read_text() == "alias ll='ls -l'\n"


def test_powerline_loaded_by_the_users_own_line_is_left_alone(isolated_home, powerline_installed, monkeypatch):
    monkeypatch.delenv("ZDOTDIR", raising=False)
    zshrc = isolated_home / ".zshrc"
    zshrc.write_text("source /usr/share/powerline/zsh/powerline.zsh\n")
    module = ShellModule()
    assert module.capture_item("powerline", None) == {"enabled": True}
    module.apply_item("powerline", {"enabled": True}, None)
    module.apply_item("powerline", {"enabled": False}, None)  # only ever removes our own block
    assert zshrc.read_text() == "source /usr/share/powerline/zsh/powerline.zsh\n"


def test_powerline_is_not_added_without_the_package(isolated_home, tmp_path, monkeypatch):
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shell, "POWERLINE_ZSH", tmp_path / "missing.zsh")
    ShellModule().apply_item("powerline", {"enabled": True}, None)
    assert not (isolated_home / ".zshrc").exists()


@pytest.mark.skipif(shutil.which("fish") is None, reason="fish is not installed")
def test_tide_settings_roundtrip(isolated_home, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    functions = isolated_home / ".config/fish/functions"
    functions.mkdir(parents=True)
    module = ShellModule()
    assert module.capture_item("tide", None) == {"installed": False, "settings": {}}

    (functions / "tide.fish").write_text("function tide; end\n")
    wanted = {"tide_left_prompt_items": ["os", "pwd", "git"], "tide_pwd_icon": [""],
              "tide_character_icon": ["❯ x"], "tide_empty": []}
    module.apply_item("tide", {"installed": True, "settings": wanted}, None)
    assert module.capture_item("tide", None) == {"installed": True, "settings": wanted}


def test_default_shell_is_captured_but_never_changed_silently():
    module = ShellModule()
    captured = module.capture_item("default_shell", None)
    assert captured == {"shell": shell.current_shell()}
    module.apply_item("default_shell", {"shell": "sh" if captured["shell"] != "sh" else "bash"}, None)
    assert module.capture_item("default_shell", None) == captured
    assert module.is_default("default_shell", {"shell": "bash"})


# -- what to ask -----------------------------------------------------------------------------------

def profile(**items):
    return {"version": 2, "modules": {"shell": {"items": items}}}


def test_find_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(shell, "current_shell", lambda: "bash")
    monkeypatch.setattr(shell, "shell_path", lambda name: None if name == "fish" else f"/usr/bin/{name}")
    monkeypatch.setattr(shell, "tide_installed", lambda: False)
    monkeypatch.setattr(shell, "POWERLINE_ZSH", tmp_path / "missing")
    monkeypatch.setattr(shell_setup.shutil, "which", lambda name: None if name == "fish" else f"/usr/bin/{name}")
    cloud = profile(default_shell={"shell": "fish"}, tide={"installed": True, "settings": {}},
                    powerline={"enabled": True})

    steps = shell_setup.find_needed(cloud, [], [])
    assert [s["id"] for s in steps] == ["shell:fish", "tide", "powerline"]
    assert [s["packages"] for s in steps] == [["fish"], ["fish"], ["powerline"]]

    assert shell_setup.find_needed(cloud, ["shell"], []) == []
    assert [s["id"] for s in shell_setup.find_needed(cloud, ["shell.tide"], ["powerline"])] == ["shell:fish"]
    monkeypatch.setattr(shell, "current_shell", lambda: "fish")
    assert [s["id"] for s in shell_setup.find_needed(cloud, [], ["tide", "powerline"])] == []
    assert shell_setup.find_needed(profile(default_shell={"shell": "rm -rf"}), [], []) == []


def test_one_password_prompt_installs_then_switches(monkeypatch):
    monkeypatch.setattr(shell, "shell_path", lambda name: None)
    steps = [{"id": "shell:fish", "packages": ["fish"], "shell": "fish"},
             {"id": "powerline", "packages": ["powerline", "zsh"]}]
    command = shell_setup.admin_command(steps, "me")
    assert command[:3] == ["pkexec", "/bin/sh", "-c"]
    assert command[4:] == ["sh", "fish powerline zsh", "/usr/bin/fish", "me"]
    assert shell_setup.admin_command([{"id": "tide", "packages": [], "tide": True}], "me") is None
    assert shell_setup.admin_command([{"id": "x", "packages": ["evil; rm"]}], "me") is None


def test_run_reports_each_step(monkeypatch):
    calls = []
    installed = []
    monkeypatch.setattr(shell, "tide_installed", lambda: bool(installed))

    def runner(argv, **_kw):
        calls.append(argv[0])
        if argv[0] == "fish":
            installed.append(True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    steps = [{"id": "shell:fish", "packages": [], "shell": "fish"}, {"id": "tide", "packages": [], "tide": True}]
    assert shell_setup.run(steps, "me", runner) == {"shell:fish": "done", "tide": "done"}
    assert calls == ["pkexec", "fish"]

    cancelled = shell_setup.run(steps[:1], "me", lambda *a, **k: SimpleNamespace(returncode=126, stdout="", stderr=""))
    assert cancelled == {"shell:fish": "cancelled"}
    result = shell_setup.build_result(["shell:fish", "tide"], {"shell:fish"}, cancelled)
    assert result == {"done": [], "declined": ["shell:fish", "tide"], "failed": {}}


# -- the service's side ------------------------------------------------------------------------------

def test_shell_prompter_asks_once_and_remembers(monkeypatch):
    monkeypatch.setattr(shell_setup, "find_needed",
                        lambda profile, disabled, declined: [] if "tide" in declined else [{"id": "tide"}])
    launched, forgotten = [], []
    process = SimpleNamespace(returncode=0, running=True)
    process.poll = lambda: None if process.running else 0

    def launch(request, result):
        launched.append(json.loads(request.read_text()))
        process.result = result
        return process

    prompter = daemon.ShellPrompter(launch=launch, clock=lambda: 0.0)
    cfg = config_module.Config(device_id="me")
    engine = SimpleNamespace(last_remote={}, forget_items=forgotten.extend)
    prompter.check(cfg, engine, None)
    prompter.check(cfg, engine, None)
    assert launched == [{"steps": [{"id": "tide"}]}]

    process.result.write_text(json.dumps({"done": [], "declined": ["tide"], "failed": {}}))
    process.running = False
    prompter.check(cfg, engine, None)
    assert config_module.load().declined_shell_setup == ["tide"] and len(launched) == 1
