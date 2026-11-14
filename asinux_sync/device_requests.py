"""Devices asking each other for things through one shared file in the cloud.

  asinux-sync/requests.json         every open request, of every kind
  asinux-sync/requests/<id>.tar.gz  what a device sent in answer to one

A request is {"id", "kind", "key", "data", "requester", "requester_name",
"status", "requested_at", ...}. What a kind means is up to its handler (see
RequestHandler); the board only moves requests and payloads around:

1. Every cycle each device asks its handlers what it lacks and adds a
   "pending" request for whatever isn't on the board yet. Its requests that
   are no longer needed are taken off.
2. A device that can answer someone else's pending request packs the answer,
   uploads it as requests/<id>.tar.gz and marks the request "sent" (or
   "failed", with a "reason", if it can't be packed).
3. The requester downloads the payload, lets its handler install it and
   takes the request and the payload off the board.

All devices write the same file, and cloud drives have no locking, so two
writes at the same moment can lose one of them. That is made harmless rather
than prevented: changes are applied to a freshly read copy just before
writing (a window of a second or so), and every step is repeated by the
next cycle if it went missing - a lost request is asked again, a lost
removal is redone, and "sent" is read from the payload being there, not from
the status, so a lost status update doesn't matter either.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from . import registry as registry_module
from .cloud.rclone import RcloneError

logger = logging.getLogger(__name__)

BOARD_PATH = f"{registry_module.ROOT}/requests.json"
PAYLOAD_DIR = f"{registry_module.ROOT}/requests"
VERSION = 1

PENDING, SENT, FAILED = "pending", "sent", "failed"


def request_id(requester: str, kind: str, key: str) -> str:
    return f"{requester}--{kind}--" + (re.sub(r"[^A-Za-z0-9._-]+", "_", key) or "_")


def payload_path(rid: str) -> str:
    return f"{PAYLOAD_DIR}/{rid}.tar.gz"


class RequestHandler:
    """One kind of thing devices can ask each other for. Subclass and pass
    an instance to RequestBoard.

    `data` is the handler's own description of one thing (JSON-serializable),
    `ctx` gives access to `cfg` and `engine`."""

    kind: str = ""

    def wanted(self, ctx) -> dict[str, dict]:
        """What this device lacks now: key -> data. The key identifies the
        thing within this kind (e.g. "icon_theme/Papirus")."""
        return {}

    def satisfied(self, data: dict, ctx) -> bool:
        """Is it here now (received, or installed some other way)?"""
        raise NotImplementedError

    def can_send(self, data: dict, ctx) -> bool:
        """Does this device have it, to send to the one asking?"""
        raise NotImplementedError

    def pack(self, data: dict, destination: Path, ctx) -> str | None:
        """Write it as tar.gz to `destination`. Returns why it can't, or None."""
        raise NotImplementedError

    def receive(self, data: dict, archive: Path, ctx) -> bool:
        """Install it from the downloaded payload. False: it wasn't in there."""
        raise NotImplementedError

    def done(self, data: dict, ctx) -> None:
        """Called once a request is closed because it's here now."""


class Context:
    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine


class RequestBoard:
    """Run once per sync cycle, after SyncEngine.sync_once()."""

    def __init__(self, handlers: list[RequestHandler]):
        self.handlers = {h.kind: h for h in handlers}

    # -- the shared file --------------------------------------------------------------

    @staticmethod
    def load(backend) -> dict[str, dict]:
        if not backend.exists_file(BOARD_PATH):
            return {}
        try:
            board = json.loads(backend.cat(BOARD_PATH))
        except ValueError:
            logger.warning("%s is not valid JSON - starting it afresh", BOARD_PATH)
            return {}
        return dict(board.get("requests", {}))

    @classmethod
    def _commit(cls, backend, ops: list[tuple[str, str, dict | None]]) -> None:
        """Apply ops to the board as it is now in the cloud, and save it."""
        if not ops:
            return
        requests = cls.load(backend)
        before = json.dumps(requests, sort_keys=True)
        for op, rid, value in ops:
            if op == "put":
                requests[rid] = value
            elif op == "update" and rid in requests:
                requests[rid] = {**requests[rid], **value}
            elif op == "delete":
                requests.pop(rid, None)
        if json.dumps(requests, sort_keys=True) != before:
            backend.rcat(BOARD_PATH, json.dumps({"version": VERSION, "requests": requests}, indent=2).encode())

    @staticmethod
    def _payloads(backend) -> set[str]:
        return {
            e["Name"][: -len(".tar.gz")]
            for e in backend.lsjson(PAYLOAD_DIR)
            if not e.get("IsDir") and e["Name"].endswith(".tar.gz")
        }

    @staticmethod
    def _delete_payload(backend, rid: str) -> None:
        try:
            backend.delete_file(payload_path(rid))
        except RcloneError as exc:
            logger.debug("couldn't delete the payload of %s: %s", rid, exc)

    # -- one cycle ------------------------------------------------------------------------

    def check(self, cfg, engine, backend, registry) -> None:
        ctx = Context(cfg, engine)
        requests = self.load(backend)
        payloads = self._payloads(backend)
        ops: list[tuple[str, str, dict | None]] = []

        for rid in payloads - set(requests):  # its request is gone: nobody will fetch it
            self._delete_payload(backend, rid)

        mine = {rid: r for rid, r in requests.items() if r.get("requester") == cfg.device_id}
        others = {rid: r for rid, r in requests.items() if r.get("requester") != cfg.device_id}
        self._receive(mine, payloads, ctx, backend, ops)
        if others:
            active = {d["id"] for d in registry.list_devices()}
            self._send(others, payloads, active, ctx, backend, ops)
        self._ask(mine, payloads, ctx, backend, ops)
        self._commit(backend, ops)

    def _close(self, rid: str, payloads: set[str], backend, ops) -> None:
        ops.append(("delete", rid, None))
        if rid in payloads:
            self._delete_payload(backend, rid)

    def _receive(self, mine, payloads, ctx, backend, ops) -> None:
        for rid, request in list(mine.items()):
            handler = self.handlers.get(request.get("kind"))
            if handler is None:
                continue  # a kind this version doesn't know: leave it alone
            data = request.get("data", {})
            if handler.satisfied(data, ctx):
                self._close(rid, payloads, backend, ops)
                del mine[rid]
                handler.done(data, ctx)
                continue
            if rid not in payloads:
                continue
            with tempfile.TemporaryDirectory(prefix="asinux-sync-request-") as tmp:
                archive = Path(tmp) / "payload.tar.gz"
                backend.download(payload_path(rid), str(archive))
                received = handler.receive(data, archive, ctx)
            if received:
                logger.info("received %s %s from %s", request["kind"], request.get("key"),
                            request.get("sent_by_name", "another device"))
                self._close(rid, payloads, backend, ops)
                del mine[rid]
                handler.done(data, ctx)
            else:
                ops.append(("update", rid, {"status": FAILED, "reason": "what was sent did not contain it"}))
                self._delete_payload(backend, rid)
                mine[rid] = {**request, "status": FAILED}

    def _send(self, others, payloads, active, ctx, backend, ops) -> None:
        for rid, request in others.items():
            if request.get("requester") not in active:
                self._close(rid, payloads, backend, ops)  # that device was removed
                continue
            handler = self.handlers.get(request.get("kind"))
            data = request.get("data", {})
            if (
                handler is None
                or request.get("status") != PENDING
                or rid in payloads  # already sent, by this device or another
                or not handler.can_send(data, ctx)
            ):
                continue
            with tempfile.TemporaryDirectory(prefix="asinux-sync-request-") as tmp:
                archive = Path(tmp) / "payload.tar.gz"
                try:
                    problem = handler.pack(data, archive, ctx)
                except OSError as exc:
                    problem = str(exc)
                if problem is None:
                    backend.copyto(str(archive), payload_path(rid))
            sender = {"sent_by": ctx.cfg.device_id, "sent_by_name": ctx.cfg.device_name,
                      "sent_at": registry_module.now_iso()}
            if problem is None:
                ops.append(("update", rid, {**sender, "status": SENT}))
                logger.info("sent %s %s to %s", request["kind"], request.get("key"),
                            request.get("requester_name", "another device"))
            else:
                ops.append(("update", rid, {**sender, "status": FAILED, "reason": problem}))
                logger.warning("can't send %s %s: %s", request["kind"], request.get("key"), problem)

    def _ask(self, mine, payloads, ctx, backend, ops) -> None:
        for kind, handler in self.handlers.items():
            wanted = handler.wanted(ctx)
            for key, data in sorted(wanted.items()):
                rid = request_id(ctx.cfg.device_id, kind, key)
                if rid in mine:
                    continue
                logger.info("asking the other devices for %s %s", kind, key)
                ops.append(("put", rid, {
                    "id": rid,
                    "kind": kind,
                    "key": key,
                    "data": data,
                    "requester": ctx.cfg.device_id,
                    "requester_name": ctx.cfg.device_name,
                    "status": PENDING,
                    "requested_at": registry_module.now_iso(),
                }))
            for rid, request in mine.items():
                if request.get("kind") == kind and request.get("key") not in wanted:
                    self._close(rid, payloads, backend, ops)  # no longer needed here
