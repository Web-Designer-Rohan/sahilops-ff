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
    status     TEXT NOT NULL DEFAULT 'active',
    error_count INTEGER NOT NULL DEFAULT 0,
    status_reason TEXT,
    proxy      TEXT,
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


# Health/proxy columns added after the first release; older databases are
# altered in place on open so no manual migration step is needed.
_ACCOUNT_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "error_count": "INTEGER NOT NULL DEFAULT 0",
    "status_reason": "TEXT",
    "proxy": "TEXT",
}
ACCOUNT_STATUSES = ("active", "suspended", "quarantined")


def _ensure_account_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)")}
    for name, declaration in _ACCOUNT_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {declaration}")


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
        _ensure_account_columns(conn)
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

def _account_row(conn: sqlite3.Connection, uid: Optional[str] = None,
                 token: Optional[str] = None) -> Optional[sqlite3.Row]:
    columns = "id, uid, token, status, error_count, status_reason, proxy"
    if uid:
        row = conn.execute(
            f"SELECT {columns} FROM accounts WHERE uid = ?", (str(uid),)
        ).fetchone()
        if row or not token:
            return row
    if token:
        return conn.execute(
            f"SELECT {columns} FROM accounts WHERE token = ?", (str(token),)
        ).fetchone()
    return None


def _record(row: sqlite3.Row) -> dict:
    rec = _loads(row["data"])
    if not isinstance(rec, dict):
        rec = {}
    rec["uid"] = row["uid"] or rec.get("uid", "")
    rec["token"] = row["token"] or rec.get("token", "")
    rec["status"] = row["status"] or "active"
    rec["error_count"] = int(row["error_count"] or 0)
    if row["status_reason"]:
        rec["status_reason"] = row["status_reason"]
    rec["proxy"] = row["proxy"] or rec.get("proxy") or ""
    return rec


def list_accounts(status: Optional[str] = None) -> List[dict]:
    query = "SELECT data, uid, token, status, error_count, status_reason, proxy FROM accounts"
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (str(status),)
    query += " ORDER BY id"
    with _lock:
        rows = _connection().execute(query, params).fetchall()
    return [_record(r) for r in rows]


def get_account(uid: Optional[str] = None, token: Optional[str] = None) -> Optional[dict]:
    with _lock:
        conn = _connection()
        row = _account_row(conn, uid, token)
        if row is None:
            return None
        full = conn.execute(
            "SELECT data, uid, token, status, error_count, status_reason, proxy "
            "FROM accounts WHERE id = ?", (row["id"],)
        ).fetchone()
    return _record(full) if full else None


def upsert_account(rec: dict) -> dict:
    if not isinstance(rec, dict):
        raise ValueError("account record must be a dict")
    uid = str(rec.get("uid") or "").strip() or None
    token = str(rec.get("token") or "").strip() or None
    if not uid and not token:
        raise ValueError("account record needs a uid or token")

    status = str(rec.get("status") or "").strip().lower()
    if status and status not in ACCOUNT_STATUSES:
        raise ValueError(f"unknown account status '{status}'")
    proxy = str(rec.get("proxy") or "").strip()
    reason = rec.get("status_reason")

    payload = _dumps(rec)
    now = time.time()
    with _lock:
        conn = _connection()
        with conn:
            row = _account_row(conn, uid, token)

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
                    "INSERT INTO accounts (uid, token, data, status, error_count, "
                    "status_reason, proxy, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, token, payload, status or "active", 0,
                     reason if status else None, proxy or None, now),
                )
            else:
                new_status = status or (row["status"] or "active")
                new_reason = reason if "status_reason" in rec else row["status_reason"]
                if new_status == "active":
                    new_reason = None
                new_proxy = proxy if "proxy" in rec else (row["proxy"] or "")
                conn.execute(
                    "UPDATE accounts SET uid=?, token=?, data=?, status=?, status_reason=?, "
                    "proxy=?, updated_at=? WHERE id=?",
                    (uid, token, payload, new_status, new_reason,
                     new_proxy or None, now, row["id"]),
                )
    return rec


def set_account_status(uid: Optional[str] = None, token: Optional[str] = None,
                       status: str = "active", reason: Optional[str] = None) -> int:
    """Set health status. Moving back to ``active`` clears the error counter."""
    status = str(status or "").strip().lower()
    if status not in ACCOUNT_STATUSES:
        raise ValueError(f"unknown account status '{status}'")
    with _lock:
        conn = _connection()
        row = _account_row(conn, uid, token)
        if row is None:
            return 0
        with conn:
            conn.execute(
                "UPDATE accounts SET status=?, status_reason=?, "
                "error_count=CASE WHEN ?='active' THEN 0 ELSE error_count END, "
                "updated_at=? WHERE id=?",
                (status, None if status == "active" else reason, status, time.time(), row["id"]),
            )
    return 1


def bump_account_error(uid: Optional[str] = None, token: Optional[str] = None,
                       reason: Optional[str] = None, threshold: int = 5) -> dict:
    """Count one failure, quarantining the account once ``threshold`` is reached."""
    with _lock:
        conn = _connection()
        row = _account_row(conn, uid, token)
        if row is None:
            return {"found": False, "error_count": 0, "status": "", "quarantined": False}
        count = int(row["error_count"] or 0) + 1
        status = row["status"] or "active"
        quarantined = False
        if status == "active" and count >= max(1, int(threshold)):
            status = "quarantined"
            reason = reason or f"{count} consecutive errors"
            quarantined = True
        new_reason = None if status == "active" else (reason or row["status_reason"])
        with conn:
            conn.execute(
                "UPDATE accounts SET error_count=?, status=?, status_reason=?, updated_at=? "
                "WHERE id=?",
                (count, status, new_reason, time.time(), row["id"]),
            )
    return {"found": True, "error_count": count, "status": status,
            "quarantined": quarantined}


def reset_account_errors(uid: Optional[str] = None, token: Optional[str] = None) -> int:
    """Clear the failure counter after a healthy connection or login."""
    with _lock:
        conn = _connection()
        row = _account_row(conn, uid, token)
        if row is None:
            return 0
        with conn:
            conn.execute(
                "UPDATE accounts SET error_count=0, updated_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
    return 1


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


# ─────────────────────────── key/value settings ───────────────────────────

def kv_get(key: str, default: Any = None) -> Any:
    """Read a JSON value from the settings table."""
    with _lock:
        row = _connection().execute(
            "SELECT value FROM kv WHERE key = ?", (str(key),)
        ).fetchone()
    if row is None:
        return default
    try:
        return _loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def kv_set(key: str, value: Any) -> None:
    """Persist a JSON value in the settings table."""
    with _lock:
        conn = _connection()
        with conn:
            conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(key), _dumps(value), time.time()),
            )


def kv_delete(key: str) -> int:
    with _lock:
        conn = _connection()
        with conn:
            cur = conn.execute("DELETE FROM kv WHERE key = ?", (str(key),))
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
