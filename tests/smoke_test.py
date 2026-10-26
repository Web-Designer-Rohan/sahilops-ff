#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SahilOps smoke test — storage, health, proxies, alerts, dashboard, protocol helpers.

Runs against a throwaway SQLite database and a real dashboard bound to
127.0.0.1:20333, so it exercises the same code paths the bot uses.

    .venv/bin/python tests/smoke_test.py [--port 20333]

Exits 0 only if every check passes. No pytest required.
"""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from aiohttp import web

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

WORK_DIR = tempfile.mkdtemp(prefix="sahilops-smoke-")
os.environ["SAHILOPS_DB"] = os.path.join(WORK_DIR, "smoke.db")

import httpx

from core import health, notifications, storage
from core import proxy as proxy_support
from core import thunderFF_pb2
from core.app import BR_HEX, _squad_target_size, _start_worker, apply_region_override
from dashboard.dashboard_server import (
    API_KEY,
    bot_state,
    redact_secrets,
    redact_text,
    start_web_dashboard,
)

CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))


def legacy_fixture():
    """Write legacy JSON files into a fresh directory for the migration check."""
    root = os.path.join(WORK_DIR, "legacy")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "accounts.json"), "w", encoding="utf-8") as f:
        json.dump([
            {"uid": "legacy-1", "password": "pw1"},
            {"uid": "legacy-2", "password": "pw2"},
            {"nonsense": True},
        ], f)
    with open(os.path.join(root, "devices.json"), "w", encoding="utf-8") as f:
        json.dump({"legacy-1": {"brand": "Samsung", "raw": [104, 105]}}, f)
    with open(os.path.join(root, "token_cache.json"), "w", encoding="utf-8") as f:
        json.dump({"legacy-1": {"token": "abc", "cached_at": 1.0}}, f)
    return root


def test_storage():
    root = legacy_fixture()
    db = os.path.join(WORK_DIR, "migrated.db")
    counts = storage.init(path=db, migrate_from=root)
    check("storage: schema created", counts == {"accounts": 2, "devices": 1, "tokens": 1}, str(counts))

    accounts = storage.list_accounts()
    check("storage: legacy accounts imported",
          [a.get("uid") for a in accounts] == ["legacy-1", "legacy-2"], str(accounts))

    dev = storage.device_get("legacy-1")
    check("storage: device round-trip preserves lists", dev and dev.get("raw") == [104, 105], str(dev))
    check("storage: token round-trip", (storage.token_get("legacy-1") or {}).get("token") == "abc")

    storage.upsert_account({"uid": "legacy-1", "password": "new-pw"})
    records = {a["uid"]: a for a in storage.list_accounts()}
    check("storage: upsert replaces instead of duplicating",
          len(records) == 2 and records["legacy-1"]["password"] == "new-pw", str(records))

    storage.upsert_account({"uid": "legacy-2", "token": "tok-2"})
    records = {a["uid"]: a for a in storage.list_accounts()}
    check("storage: uid+token upsert keeps one row", len(records) == 2, str(records))

    # Concurrent writers must not lose updates (this is what the JSON files did).
    def writer(n):
        for i in range(20):
            storage.upsert_account({"uid": f"bulk-{n}-{i}", "password": "pw"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(8)))
    bulk = [a for a in storage.list_accounts() if str(a.get("uid", "")).startswith("bulk-")]
    check("storage: 160 concurrent upserts all persisted", len(bulk) == 160, f"{len(bulk)} rows")

    check("storage: remove_account by uid", storage.remove_account(uid="legacy-1") == 1)
    check("storage: remove_account by token",
          storage.remove_account(token="tok-2") == 1)
    storage.device_delete("legacy-1")
    check("storage: device delete", storage.device_get("legacy-1") is None)
    storage.token_delete("legacy-1")
    check("storage: token delete", storage.token_get("legacy-1") is None)

    for acc in storage.list_accounts():
        storage.remove_account(uid=str(acc.get("uid", "")) or None,
                               token=str(acc.get("token", "")) or None)
    check("storage: cleanup leaves an empty table", storage.list_accounts() == [])


def test_storage_health():
    db = os.path.join(WORK_DIR, "old-schema.db")
    empty = os.path.join(WORK_DIR, "no-legacy")
    os.makedirs(empty, exist_ok=True)

    # A database created before the health/proxy columns existed.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "uid TEXT UNIQUE COLLATE NOCASE, token TEXT UNIQUE, data TEXT NOT NULL, "
        "updated_at REAL NOT NULL);"
        "INSERT INTO accounts (uid, token, data, updated_at) "
        "VALUES ('old-1', 'tok-old', '{}', 0);"
    )
    conn.commit()
    conn.close()

    storage.close()
    storage.init(path=db, migrate_from=empty)
    rec = storage.get_account(uid="old-1")
    check("storage: old schema gains health/proxy columns",
          bool(rec) and rec["status"] == "active" and rec["error_count"] == 0
          and "proxy" in rec, str(rec))

    storage.set_account_status(uid="old-1", status="suspended", reason="account banned")
    rec = storage.get_account(uid="old-1")
    check("storage: suspension persisted with reason",
          rec["status"] == "suspended" and rec["status_reason"] == "account banned", str(rec))

    storage.set_account_status(uid="old-1", status="active")
    rec = storage.get_account(uid="old-1")
    check("storage: reactivation clears the reason",
          rec["status"] == "active" and not rec.get("status_reason"), str(rec))

    counts = [storage.bump_account_error(uid="old-1", reason="boom", threshold=3)
              for _ in range(3)]
    check("storage: errors are counted before the threshold",
          counts[0]["error_count"] == 1 and counts[1]["error_count"] == 2
          and not counts[1]["quarantined"], str(counts))
    check("storage: threshold quarantines the account",
          counts[2]["quarantined"] and counts[2]["status"] == "quarantined", str(counts[2]))

    storage.reset_account_errors(uid="old-1")
    check("storage: reset clears the error counter",
          storage.get_account(uid="old-1")["error_count"] == 0)

    storage.upsert_account({"uid": "proxy-1", "password": "pw",
                            "proxy": "socks5://user:pass@10.0.0.1:1080"})
    rec = storage.get_account(uid="proxy-1")
    check("storage: per-account proxy persisted",
          rec and rec.get("proxy") == "socks5://user:pass@10.0.0.1:1080", str(rec))

    storage.remove_account(uid="old-1")
    storage.remove_account(uid="proxy-1")
    check("storage: health fixtures cleaned up", storage.list_accounts() == [])


def test_health_detection():
    positives = {
        "{\"error\":\"account is banned\"}": "account banned",
        "Account suspended for cheating": "account suspended",
        "account has been restricted": "account restricted",
        "{\"ban\": 1}": "flag",
        "{\"error_code\": 1004}": "code 1004",
        "permanently banned": "permanently banned",
    }
    for text, expected in positives.items():
        reason = health.detect_suspension(text)
        check(f"health: detects '{text[:32]}'",
              bool(reason) and expected in str(reason), str(reason))

    for text in ("banner advertisement loaded", "match completed", "connection reset", ""):
        check(f"health: no false positive for '{text or 'empty'}'",
              health.detect_suspension(text) is None, str(health.detect_suspension(text)))

    check("health: binary payload does not crash detection",
          health.detect_suspension(b"\xff\xfe account is banned") is not None)
    check("health: quarantine statuses recognised",
          health.is_quarantine_status("suspended")
          and health.is_quarantine_status("QUARANTINED")
          and not health.is_quarantine_status("active"))

    rec = {"uid": "blocked-worker", "password": "pw", "status": "quarantined"}
    task = _start_worker(rec)
    check("health: quarantined account starts no worker",
          task is None and "blocked-worker" not in bot_state.account_workers)


def test_proxy_helpers():
    info = proxy_support.parse_proxy("socks5://user:p%40ss@10.0.0.5:1080")
    check("proxy: url parsed with credentials",
          info and info["scheme"] == "socks5" and info["host"] == "10.0.0.5"
          and info["port"] == 1080 and info["password"] == "p@ss", str(info))
    check("proxy: credentials masked in labels",
          proxy_support.describe_proxy("http://user:secret@10.0.0.5:8080")
          == "http://***@10.0.0.5:8080")
    check("proxy: empty string means direct",
          proxy_support.normalize_proxy("") is None
          and proxy_support.normalize_proxy(None) is None)
    for bad in ("ftp://10.0.0.5:21", "10.0.0.5:1080", "socks5://10.0.0.5"):
        try:
            proxy_support.normalize_proxy(bad)
            raised = False
        except ValueError:
            raised = True
        check(f"proxy: rejects '{bad}'", raised)


async def _start_fake_proxy(kind):
    """Minimal CONNECT / SOCKS5 server that echoes tunnelled bytes."""
    seen = {}

    async def handle(reader, writer):
        try:
            if kind == "http":
                head = await reader.readuntil(b"\r\n\r\n")
                seen["request"] = head.decode("latin-1", "replace").split("\r\n", 1)[0]
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                greeting = await reader.readexactly(2)
                await reader.readexactly(greeting[1])
                writer.write(b"\x05\x00")
                await writer.drain()
                request = await reader.readexactly(4)
                atyp = request[3]
                if atyp == 1:
                    host = socket.inet_ntoa(await reader.readexactly(4))
                elif atyp == 3:
                    length = (await reader.readexactly(1))[0]
                    host = (await reader.readexactly(length)).decode()
                else:
                    host = "?"
                port = struct.unpack(">H", await reader.readexactly(2))[0]
                seen["request"] = f"{host}:{port}"
                writer.write(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
                await writer.drain()
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, seen


async def test_proxy_tunnels():
    for kind in ("http", "socks5h"):
        server, seen = await _start_fake_proxy(kind)
        port = server.sockets[0].getsockname()[1]
        writer = None
        try:
            reader, writer = await proxy_support.open_connection(
                "gateway.example.com", 20333, f"{kind}://127.0.0.1:{port}", timeout=5.0)
            writer.write(b"ping")
            await writer.drain()
            echoed = await asyncio.wait_for(reader.readexactly(4), timeout=5.0)
            check(f"proxy: {kind} tunnel carries gateway traffic", echoed == b"ping", str(echoed))
            check(f"proxy: {kind} CONNECT target preserved",
                  "gateway.example.com:20333" in seen.get("request", ""),
                  seen.get("request", ""))
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            server.close()
            await server.wait_closed()


async def test_notifications():
    received = []

    async def hook(request):
        received.append(await request.json())
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/hook", hook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/hook"

    notifier = notifications.Notifier(url=url, min_interval=0.0)
    check("alerts: notifier enabled for an http webhook", notifier.enabled)
    check("alerts: disabled without a URL", not notifications.Notifier(url="").enabled)
    check("alerts: telegram needs a chat id",
          not notifications.Notifier(url="https://api.telegram.org/botX/sendMessage").enabled)
    check("alerts: webhook kind classified",
          notifications.classify_webhook("https://discord.com/api/webhooks/1/x") == "discord"
          and notifications.classify_webhook(
              "https://api.telegram.org/bot1/sendMessage") == "telegram")

    telegram = notifications.build_payload(
        "https://api.telegram.org/botX/sendMessage", "hello", "42", "level_up")
    check("alerts: telegram payload shape",
          telegram.get("chat_id") == "42" and telegram.get("text") == "hello", str(telegram))
    discord = notifications.build_payload(
        "https://discord.com/api/webhooks/1/x", "hello", None, "level_up")
    check("alerts: discord payload shape", discord.get("content") == "hello", str(discord))

    try:
        sent = await notifier.notify("level_up", "Level 5 → 6", uid="42",
                                     token="secret-token-value")
        check("alerts: alert queued", sent is True)
        await notifier.flush(timeout=5.0)
        check("alerts: webhook received the alert",
              len(received) == 1 and "Level 5" in received[0].get("text", ""),
              str(received))
        check("alerts: event name and account included",
              received and received[0].get("event") == "level_up"
              and "account 42" in received[0].get("text", ""), str(received[:1]))
        check("alerts: token-shaped text scrubbed before sending",
              "secret-token-value" not in json.dumps(received), str(received[:1]))
        check("alerts: delivery counted", notifier.sent_count == 1, str(notifier.sent_count))
    finally:
        await notifier.close(timeout=5.0)
        await runner.cleanup()


def test_squad_sizing():
    expected = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 9: 4}
    actual = {n: _squad_target_size(n) for n in expected}
    check("squad: flexible target size clamps to [1, TEAM_SIZE]",
          actual == expected, f"actual={actual} expected={expected}")


def test_redaction_helpers():
    payload = {
        "uid": "123",
        "nickname": "Player",
        "token": "eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiIxMjMifQ.c2lnbmF0dXJl",
        "access_token": "abc",
        "nested": {"password": "pw", "aes_key": "00" * 16, "level": 7},
        "items": [{"session_token": "s"}, "Bearer abc.def-ghi"],
    }
    safe = redact_secrets(payload)
    check("redaction: secret keys dropped recursively",
          "token" not in safe and "access_token" not in safe
          and "password" not in safe["nested"] and "aes_key" not in safe["nested"]
          and "session_token" not in safe["items"][0], str(safe))
    check("redaction: ordinary fields survive",
          safe["uid"] == "123" and safe["nested"]["level"] == 7)
    check("redaction: bearer text scrubbed", "abc.def-ghi" not in str(safe["items"][1]), str(safe))
    check("redaction: key=value text scrubbed",
          redact_text("token=abc123 password=hunter2 ok") == "token=[REDACTED] password=[REDACTED] ok",
          redact_text("token=abc123 password=hunter2 ok"))


def test_region_override():
    packet = bytes.fromhex(BR_HEX)
    for region in ("ID", "SG", "IND"):
        out = apply_region_override(packet, region, "1.132.6")
        proto = thunderFF_pb2.StartMatch()
        proto.ParseFromString(out)
        regions = {entry.region for entry in proto.main.region_list}
        ok = regions == {region} and proto.main.client_version.remote_version == "1.132.6"
        check(f"region: override applied for {region}", ok, str(regions))

    check("region: unparsable packet returned unchanged",
          apply_region_override(b"\xff\xff\xff", "SG") == b"\xff\xff\xff")


async def test_dashboard(port):
    calls = {"added": [], "deleted": [], "restarted": [], "paused": []}
    restart_done = asyncio.Event()

    async def on_added(data):
        calls["added"].append(data)

    async def on_deleted(ids):
        calls["deleted"].append(ids)

    async def on_restart(uid):
        calls["restarted"].append(uid)
        restart_done.set()

    async def on_pause(uid, paused):
        calls["paused"].append((uid, paused))

    bot_state.refresh_callbacks.update({
        "on_account_added": on_added,
        "on_account_deleted": on_deleted,
        "on_restart_account": on_restart,
        "on_pause_toggle": on_pause,
    })

    runner = await start_web_dashboard(host="127.0.0.1", port=port)
    base = f"http://127.0.0.1:{port}"
    auth = {"X-API-Key": API_KEY}
    try:
        # Unauthenticated client: every write route must be rejected.
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as anon:
            for path in ("/api/account/add", "/api/account/delete", "/api/account/refresh",
                         "/api/account/restart", "/api/account/pause",
                         "/api/account/pause_all", "/api/logs/clear", "/api/clanjoin"):
                res = await anon.post(path, json={"uid": "nope"})
                check(f"security: {path} rejects requests without X-API-Key",
                      res.status_code == 401, str(res.status_code))
            res = await anon.post("/api/account/add", json={"uid": "nope", "password": "x"},
                                  headers={"X-API-Key": "wrong-key"})
            check("security: wrong API key rejected", res.status_code == 401, str(res.status_code))
            check("security: unauthenticated write did not persist",
                  all(a.get("uid") != "nope" for a in storage.list_accounts()))
            res = await anon.get("/api/guildinfo", params={"guild_id": "1"})
            check("security: /api/guildinfo requires the key", res.status_code == 401, str(res.status_code))

        async with httpx.AsyncClient(base_url=base, timeout=5.0, headers=auth) as client:
            res = await client.get("/")
            check("dashboard: binds 127.0.0.1 and serves index",
                  res.status_code == 200 and "SahilOps" in res.text, str(res.status_code))
            check("security: index ships the configured key placeholder resolved",
                  "__SAHILOPS_API_KEY__" not in res.text and API_KEY in res.text)

            res = await client.get("/api/stats")
            check("dashboard: /api/stats responds", res.status_code == 200 and isinstance(res.json(), dict))
            bot_state.register_account("secret-uid", "SecretPlayer", "IND", 5, 1000,
                                       token="eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiIxMjMifQ.c2lnbmF0dXJl")
            bot_state.log("login with token=super-secret-jwt-value and password=hunter2", "info", "secret-uid")
            res = await client.get("/api/stats")
            body = res.text
            check("security: /api/stats omits account token field",
                  "eyJhbGciOiJIUzI1NiJ9" not in body and '"token"' not in body, body[:200])
            check("security: /api/stats scrubs credentials from log lines",
                  "super-secret-jwt-value" not in body and "hunter2" not in body, body[:200])
            logs_res = await client.get("/api/logs")
            check("security: /api/logs scrubs credentials",
                  "super-secret-jwt-value" not in logs_res.text and "hunter2" not in logs_res.text,
                  logs_res.text[:200])
            bot_state.accounts.pop("secret-uid", None)

            # Health and proxy annotations come from SQLite, not engine state.
            storage.upsert_account({"uid": "health-acct", "password": "pw",
                                    "proxy": "socks5://user:topsecret@10.0.0.9:1080"})
            storage.set_account_status(uid="health-acct", status="quarantined",
                                       reason="3 consecutive errors")
            bot_state.register_account("health-acct", "FlaggedPlayer", "IND", 7, 5000)
            res = await client.get("/api/stats")
            body = res.json()
            flagged = next((a for a in body["accounts"] if a.get("uid") == "health-acct"), {})
            check("dashboard: quarantined account reported by /api/stats",
                  flagged.get("health", {}).get("status") == "quarantined",
                  str(flagged.get("health")))
            check("dashboard: health summary counts flagged accounts",
                  body.get("health", {}).get("quarantined") == 1, str(body.get("health")))
            check("dashboard: proxy credentials masked in stats",
                  "topsecret" not in res.text
                  and "socks5://***@10.0.0.9:1080" in res.text, res.text[:200])
            check("dashboard: quarantine reason included for operators",
                  "3 consecutive errors" in res.text, res.text[:200])
            bot_state.accounts.pop("health-acct", None)

            # A quarantined record skipped at startup has no engine account.
            storage.upsert_account({"uid": "idle-acct", "password": "pw"})
            storage.set_account_status(uid="idle-acct", status="suspended", reason="banned")
            res = await client.get("/api/stats")
            body = res.json()
            idle = next((a for a in body["accounts"] if a.get("uid") == "idle-acct"), {})
            check("dashboard: persisted account without a worker still listed",
                  idle.get("health", {}).get("status") == "suspended", str(idle))
            storage.remove_account(uid="idle-acct")
            storage.remove_account(uid="health-acct")

            res = await client.post("/api/account/add", json={
                "uid": "proxy-uid", "password": "pw", "proxy": "http://127.0.0.1:8080"})
            check("dashboard: add account accepts a proxy", res.status_code == 200, res.text)
            saved_rec = storage.get_account(uid="proxy-uid") or {}
            check("dashboard: proxy saved with the account",
                  saved_rec.get("proxy") == "http://127.0.0.1:8080", str(saved_rec))
            res = await client.post("/api/account/add", json={
                "uid": "bad-proxy", "password": "pw", "proxy": "ftp://127.0.0.1:21"})
            check("dashboard: invalid proxy rejected", res.status_code == 400, str(res.status_code))
            await client.post("/api/account/delete", json={"uid": "proxy-uid"})

            bot_state.log("smoke-test log line", "info")
            res = await client.get("/api/logs")
            check("dashboard: /api/logs streams live log entries",
                  res.status_code == 200 and any("smoke-test log line" in line for line in res.json()["logs"]),
                  str(res.text[:200]))

            res = await client.post("/api/account/add", json={"uid": "smoke-uid", "password": "smoke-pw"})
            check("dashboard: add account accepted", res.status_code == 200 and res.json().get("status") == "ok",
                  res.text)
            saved = {a.get("uid") for a in storage.list_accounts()}
            check("dashboard: added account persisted to SQLite", "smoke-uid" in saved, str(saved))
            await asyncio.wait_for(_wait_for(lambda: calls["added"]), timeout=3.0)
            check("dashboard: add callback ran",
                  any(entry.get("uid") == "smoke-uid" for entry in calls["added"]),
                  str(calls["added"]))

            res = await client.post("/api/account/pause", json={"uid": "smoke-uid"})
            check("dashboard: pause toggles state",
                  res.status_code == 200 and res.json().get("is_paused") is True and bot_state.is_paused("smoke-uid"))

            res = await client.post("/api/account/restart", json={"uid": "smoke-uid"})
            check("dashboard: restart accepted", res.status_code == 200, res.text)
            try:
                await asyncio.wait_for(restart_done.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            check("dashboard: restart callback resumed worker", calls["restarted"] == ["smoke-uid"])

            res = await client.post("/api/account/restart", json={"uid": "ghost-account"})
            check("dashboard: restart unknown account returns 404", res.status_code == 404, str(res.status_code))

            res = await client.post("/api/account/pause", json={"uid": "smoke-uid"})
            check("dashboard: pause toggles back off",
                  res.status_code == 200 and res.json().get("is_paused") is False, res.text)

            res = await client.post("/api/account/delete", json={"uid": "smoke-uid"})
            check("dashboard: delete accepted", res.status_code == 200 and res.json().get("status") == "ok",
                  res.text)
            remaining = {a.get("uid") for a in storage.list_accounts()}
            check("dashboard: delete removed the account from SQLite", "smoke-uid" not in remaining, str(remaining))
            await asyncio.wait_for(_wait_for(lambda: calls["deleted"]), timeout=3.0)
            check("dashboard: delete callback ran", bool(calls["deleted"]))

            # Rapid add/pause/restart cycles must not deadlock the event loop.
            start = time.time()
            for i in range(10):
                uid = f"cycle-{i}"
                await client.post("/api/account/add", json={"uid": uid, "password": "pw"})
                await client.post("/api/account/pause", json={"uid": uid})
                await client.post("/api/account/restart", json={"uid": uid})
                await client.post("/api/account/delete", json={"uid": uid})
            elapsed = time.time() - start
            check("dashboard: 10 add/pause/restart/delete cycles complete without deadlock",
                  elapsed < 15.0, f"{elapsed:.1f}s")

            await asyncio.sleep(0.3)
            await bot_state.drain(timeout=5.0)
            check("dashboard: background tasks drained on shutdown",
                  not [t for t in bot_state.background_tasks if not t.done()])
    finally:
        await runner.cleanup()


async def _wait_for(predicate, interval=0.05, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=20333)
    args = parser.parse_args()

    print("=" * 64)
    print(" SahilOps smoke test")
    print("=" * 64)

    test_storage()
    test_storage_health()
    test_squad_sizing()
    test_redaction_helpers()
    test_region_override()
    test_health_detection()
    test_proxy_helpers()
    try:
        asyncio.run(test_proxy_tunnels())
    except Exception as e:
        check("proxy: tunnels run without error", False, f"{type(e).__name__}: {e}")
    try:
        asyncio.run(test_notifications())
    except Exception as e:
        check("alerts: notifier run without error", False, f"{type(e).__name__}: {e}")
    try:
        asyncio.run(test_dashboard(args.port))
    except Exception as e:
        check("dashboard: run on 127.0.0.1:%d without error" % args.port, False,
              f"{type(e).__name__}: {e}")
    finally:
        try:
            storage.close()
        except Exception:
            pass

    failed = [name for name, ok, _ in CHECKS if not ok]
    print("-" * 64)
    print(f" {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        for name in failed:
            print(f"   FAILED: {name}")
        return 1
    print(" SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
