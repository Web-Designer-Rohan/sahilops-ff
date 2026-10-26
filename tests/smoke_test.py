#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SahilOps smoke test — storage, dashboard endpoints, squad sizing, region override.

Runs against a throwaway SQLite database and a real dashboard bound to
127.0.0.1:20333, so it exercises the same code paths the bot uses.

    .venv/bin/python tests/smoke_test.py [--port 20333]

Exits 0 only if every check passes. No pytest required.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

WORK_DIR = tempfile.mkdtemp(prefix="sahilops-smoke-")
os.environ["SAHILOPS_DB"] = os.path.join(WORK_DIR, "smoke.db")

import httpx

from core import storage
from core import thunderFF_pb2
from core.app import BR_HEX, _squad_target_size, apply_region_override
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
            check("dashboard: add callback ran", calls["added"] and calls["added"][0].get("uid") == "smoke-uid")

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
    test_squad_sizing()
    test_redaction_helpers()
    test_region_override()
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
