# -*- coding: utf-8 -*-
"""SQLite-backed state store for SahilOps.

Accounts, device fingerprints, and cached sessions all live in one database so
the dashboard, the generator, and the bot engine can no longer overwrite each
other's JSON files. Access is serialized with a process-wide lock and SQLite
runs in WAL mode, so separate processes can read and write safely too.

The legacy JSON files are imported once on first start and then left alone.
"""

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("SAHILOPS_DB", os.path.join(BASE_DIR, "sahilops.db"))

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_migrate_dir = BASE_DIR
_last_migration: Dict[str, int] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT UNIQUE COLLATE NOCASE,
    token      TEXT UNIQUE,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    uid        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    key        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


# ─────────────────────────── serialization ───────────────────────────

def _encode(obj: Any) -> Any:
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {"__bytes_hex__"}:
            try:
                return bytes.fromhex(obj["__bytes_hex__"])
            except (TypeError, ValueError):
                return b""
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(_encode(obj), separators=(",", ":"), ensure_ascii=False)


def _loads(text: str) -> Any:
    return _decode(json.loads(text))


def _read_json(path: str, kind):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return kind()
    return data if isinstance(data, kind) else kind()


# ─────────────────────────── connection ───────────────────────────

def _migrate_json(conn: sqlite3.Connection) -> None:
    global _last_migration
    if conn.execute("SELECT 1 FROM kv WHERE key = 'json_migrated'").fetchone():
        return

    counts = {"accounts": 0, "devices": 0, "tokens": 0}
    now = time.time()

    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        for rec in _read_json(os.path.join(_migrate_dir, "accounts.json"), list):
            if not isinstance(rec, dict):
                continue
            uid = str(rec.get("uid") or "").strip() or None
            token = str(rec.get("token") or "").strip() or None
            if not uid and not token:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO accounts (uid, token, data, updated_at) VALUES (?,?,?,?)",
                (uid, token, _dumps(rec), now),
            )
            counts["accounts"] += 1

    if conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0:
        for uid, dev in _read_json(os.path.join(_migrate_dir, "devices.json"), dict).items():
            conn.execute(
                "INSERT OR IGNORE INTO devices (uid, data, updated_at) VALUES (?,?,?)",
                (str(uid), _dumps(dev), now),
            )
            counts["devices"] += 1

    if conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 0:
        for key, entry in _read_json(os.path.join(_migrate_dir, "token_cache.json"), dict).items():
            conn.execute(
                "INSERT OR IGNORE INTO tokens (key, data, updated_at) VALUES (?,?,?)",
                (str(key), _dumps(entry), now),
            )
            counts["tokens"] += 1

    conn.execute(
        "INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES ('json_migrated','1',?)",
        (now,),
    )
    conn.commit()
    _last_migration = counts


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        parent = os.path.dirname(DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
        _conn = conn
        _migrate_json(conn)
    return _conn


def init(path: Optional[str] = None, migrate_from: Optional[str] = None) -> Dict[str, int]:
    """Open (and create) the database. Returns current row counts."""
    global DB_PATH, _migrate_dir
    with _lock:
        if path:
            path = os.path.abspath(path)
            if path != DB_PATH:
                _close_locked()
                DB_PATH = path
        if migrate_from:
            _migrate_dir = os.path.abspath(migrate_from)
        return _counts(_connection())


def close() -> None:
    with _lock:
        _close_locked()


def _close_locked() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        finally:
            _conn = None


def _counts(conn: sqlite3.Connection) -> Dict[str, int]:
    return {
        "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
        "devices": conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
        "tokens": conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0],
    }


def last_migration() -> Dict[str, int]:
    return dict(_last_migration)


# ─────────────────────────── accounts ───────────────────────────

def list_accounts() -> List[dict]:
    with _lock:
        rows = _connection().execute("SELECT data FROM accounts ORDER BY id").fetchall()
    return [_loads(r["data"]) for r in rows]


def upsert_account(rec: dict) -> dict:
    if not isinstance(rec, dict):
        raise ValueError("account record must be a dict")
    uid = str(rec.get("uid") or "").strip() or None
    token = str(rec.get("token") or "").strip() or None
    if not uid and not token:
        raise ValueError("account record needs a uid or token")

    payload = _dumps(rec)
    now = time.time()
    with _lock:
        conn = _connection()
        with conn:
            row = None
            if uid:
                row = conn.execute("SELECT id FROM accounts WHERE uid = ?", (uid,)).fetchone()
            if row is None and token:
                row = conn.execute("SELECT id FROM accounts WHERE token = ?", (token,)).fetchone()

            # A re-add can move a uid or token between records; drop the loser.
            for column, value in (("uid", uid), ("token", token)):
                if not value:
                    continue
                other = conn.execute(
                    f"SELECT id FROM accounts WHERE {column} = ?", (value,)
                ).fetchone()
                if other and (row is None or other["id"] != row["id"]):
                    conn.execute("DELETE FROM accounts WHERE id = ?", (other["id"],))

            if row is None:
                conn.execute(
                    "INSERT INTO accounts (uid, token, data, updated_at) VALUES (?,?,?,?)",
                    (uid, token, payload, now),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET uid=?, token=?, data=?, updated_at=? WHERE id=?",
                    (uid, token, payload, now, row["id"]),
                )
    return rec


def remove_account(uid: Optional[str] = None, token: Optional[str] = None) -> int:
    clauses, params = [], []
    if uid:
        clauses.append("uid = ?")
        params.append(str(uid))
    if token:
        clauses.append("token = ?")
        params.append(str(token))
    if not clauses:
        return 0
    with _lock:
        conn = _connection()
        with conn:
            cur = conn.execute("DELETE FROM accounts WHERE " + " OR ".join(clauses), params)
    return cur.rowcount


# ─────────────────────────── devices ───────────────────────────

def devices_all() -> Dict[str, dict]:
    with _lock:
        rows = _connection().execute("SELECT uid, data FROM devices").fetchall()
    return {r["uid"]: _loads(r["data"]) for r in rows}


def device_get(uid: str) -> Optional[dict]:
    with _lock:
        row = _connection().execute(
            "SELECT data FROM devices WHERE uid = ?", (str(uid),)
        ).fetchone()
    return _loads(row["data"]) if row else None


def device_set(uid: str, dev: dict) -> None:
    with _lock:
        conn = _connection()
        with conn:
            conn.execute(
                "INSERT INTO devices (uid, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (str(uid), _dumps(dev), time.time()),
            )


def device_delete(uid: str) -> int:
    with _lock:
        conn = _connection()
        with conn:
            cur = conn.execute("DELETE FROM devices WHERE uid = ?", (str(uid),))
    return cur.rowcount


# ─────────────────────────── token cache ───────────────────────────

def tokens_all() -> Dict[str, dict]:
    with _lock:
        rows = _connection().execute("SELECT key, data FROM tokens").fetchall()
    return {r["key"]: _loads(r["data"]) for r in rows}


def token_get(key: str) -> Optional[dict]:
    with _lock:
        row = _connection().execute(
            "SELECT data FROM tokens WHERE key = ?", (str(key),)
        ).fetchone()
    return _loads(row["data"]) if row else None


def token_set(key: str, data: dict) -> None:
    with _lock:
        conn = _connection()
        with conn:
            conn.execute(
                "INSERT INTO tokens (key, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (str(key), _dumps(data), time.time()),
            )


def token_delete(key: str) -> int:
    with _lock:
        conn = _connection()
        with conn:
            cur = conn.execute("DELETE FROM tokens WHERE key = ?", (str(key),))
    return cur.rowcount
