from __future__ import annotations

import argparse
import sys

from . import config as config_module
from .cloud.rclone import RcloneBackend, RcloneError, is_installed
from .engine import ConflictError, SyncEngine
from .modules import REGISTRY


def _build_engine(cfg: config_module.Config) -> SyncEngine:
    backend = RcloneBackend(cfg.remote_name)
    modules = {mid: cls() for mid, cls in REGISTRY.items()}
    return SyncEngine(backend, modules, config_module.STATE_DIR)


def cmd_connect(args: argparse.Namespace) -> int:
    if not is_installed():
        print("rclone is not installed - install it first (e.g. `sudo dnf install rclone`).", file=sys.stderr)
        return 1

    cfg = config_module.load()
    remote_name = args.remote_name or cfg.remote_name

    def on_auth_url(url: str) -> None:
        print(f"Open this URL to sign in (a private browser window is recommended):\n  {url}\n")

    try:
        result = RcloneBackend.authorize(args.provider, on_auth_url)
        RcloneBackend.config_create(remote_name, args.provider, result.token_json)
    except RcloneError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 1

    cfg.remote_name = remote_name
    config_module.save(cfg)
    print(f"Connected '{remote_name}' ({args.provider}). Open Asinux Sync to register this device.")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    cfg = config_module.load()
    engine = _build_engine(cfg)
    module_ids = args.modules or cfg.enabled(list(REGISTRY))
    try:
        result = engine.push(module_ids, force=args.force)
    except ConflictError as exc:
        print(f"Conflict: {exc}", file=sys.stderr)
        print("Re-run with --force to overwrite, or `asinux-sync-cli pull` first to inspect the remote state.", file=sys.stderr)
        return 1
    except RcloneError as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    print(f"Pushed modules: {', '.join(result.applied_modules)}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    cfg = config_module.load()
    engine = _build_engine(cfg)
    try:
        result = engine.pull(cfg.enabled(list(REGISTRY)))
    except RcloneError as exc:
        print(f"Pull failed: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("Nothing has been pushed for this user yet.")
        return 0
    print(f"Applied modules: {', '.join(result.applied_modules)}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .daemon import tick

    try:
        print(tick(config_module.load()))
    except RcloneError as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    import logging

    from . import daemon

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    daemon.run()
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    from . import service

    try:
        {"enable": service.enable, "disable": service.disable}[args.action]()
    except service.ServiceError as exc:
        print(f"Service error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = config_module.load()
    print(f"remote:    {cfg.remote_name}")
    print(f"device:    {cfg.device_name or '(not registered)'}")
    print(f"modules:   {', '.join(cfg.enabled(list(REGISTRY)))}")
    print(f"interval:  {cfg.sync_interval_seconds}s")
    print(f"connected: {RcloneBackend.exists(cfg.remote_name) if is_installed() else 'rclone not installed'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asinux-sync-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    connect_p = sub.add_parser("connect", help="connect a cloud account via rclone")
    connect_p.add_argument("--provider", default="onedrive")
    connect_p.add_argument("--remote-name", default=None)
    connect_p.set_defaults(func=cmd_connect)

    push_p = sub.add_parser("push", help="capture local state and upload it")
    push_p.add_argument("--force", action="store_true", help="overwrite remote even if it changed since last sync")
    push_p.add_argument("--modules", nargs="*", default=None)
    push_p.set_defaults(func=cmd_push)

    pull_p = sub.add_parser("pull", help="download and apply remote state")
    pull_p.set_defaults(func=cmd_pull)

    sync_p = sub.add_parser("sync", help="run one two-way sync cycle now")
    sync_p.set_defaults(func=cmd_sync)

    daemon_p = sub.add_parser("daemon", help="run the background sync loop (used by the systemd user service)")
    daemon_p.set_defaults(func=cmd_daemon)

    service_p = sub.add_parser("service", help="enable or disable the background sync service")
    service_p.add_argument("action", choices=["enable", "disable"])
    service_p.set_defaults(func=cmd_service)

    status_p = sub.add_parser("status", help="show current config")
    status_p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
