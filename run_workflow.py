#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SahilOps unified workflow launcher.

One command takes a fresh checkout through the whole happy path:

    .venv/bin/python run_workflow.py

  1. loads ``.env`` (or ``.env.example``) before any core module is imported,
  2. verifies the virtualenv and runtime dependencies,
  3. creates/upgrades and integrity-checks ``sahilops.db``,
  4. shows a numbered region menu (India [IN], Indonesia [ID], ...),
  5. generates guest accounts **sequentially** with anti-rate-limit pacing,
  6. verifies the accounts persisted in SQLite, and
  7. starts the automation server/dashboard on 127.0.0.1:20333.

Useful non-interactive switches::

    --check                 environment + database checks only
    --region ID             skip the menu (IN/IND, ID, SG, TH, MY, VN, PH, BR, PK)
    --accounts 1 --delay 8  generation count and pacing
    --no-generate           serve the accounts already in the database
    --no-serve              generate/verify only
    --dry-run               validate and print the plan without network or server
    --allow-empty           start the dashboard even with zero accounts
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 20333
DEFAULT_DELAY = float(os.environ.get("SAHILOPS_ACCOUNT_DELAY", "8"))
DEFAULT_ACCOUNTS = 1


@dataclass(frozen=True)
class Region:
    name: str
    display: str
    protocol: str
    aliases: Tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.name} [{self.display}]"


REGIONS: Tuple[Region, ...] = (
    Region("India", "IN", "IND", ("IN", "IND", "INDIA")),
    Region("Indonesia", "ID", "ID", ("ID", "INDONESIA")),
    Region("Singapore", "SG", "SG", ("SG", "SINGAPORE")),
    Region("Thailand", "TH", "TH", ("TH", "THAILAND")),
    Region("Malaysia", "MY", "MY", ("MY", "MALAYSIA")),
    Region("Vietnam", "VN", "VN", ("VN", "VIETNAM")),
    Region("Philippines", "PH", "PH", ("PH", "PHILIPPINES")),
    Region("Brazil", "BR", "BR", ("BR", "BRAZIL")),
    Region("Pakistan", "PK", "PK", ("PK", "PAKISTAN")),
)


# ─────────────────────────── environment ───────────────────────────

def _parse_env_line(line: str) -> Optional[Tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_environment(explicit: Optional[str] = None) -> Tuple[Optional[Path], list]:
    """Load .env (or .env.example) without overriding real environment vars."""
    candidates = [Path(explicit)] if explicit else [ROOT / ".env", ROOT / ".env.example"]
    source: Optional[Path] = None
    loaded: list = []

    for candidate in candidates:
        if not candidate.is_file():
            continue
        source = candidate
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"[!] Could not read {candidate}: {exc}")
            break
        for line in lines:
            parsed = _parse_env_line(line)
            if not parsed:
                continue
            key, value = parsed
            if key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
        break

    db_path = os.environ.get("SAHILOPS_DB", "").strip()
    if db_path and not os.path.isabs(db_path):
        os.environ["SAHILOPS_DB"] = str((ROOT / db_path).resolve())
    elif not db_path:
        os.environ["SAHILOPS_DB"] = str((ROOT / "sahilops.db").resolve())

    return source, loaded


# ─────────────────────────── checks ───────────────────────────

def check_virtualenv() -> Tuple[bool, str]:
    expected = (ROOT / ".venv").resolve()
    actual = Path(sys.prefix).resolve()
    if actual == expected or expected in actual.parents:
        return True, f"active: {actual}"
    return False, f"running outside .venv ({actual}); use .venv/bin/python"


DEPENDENCIES = (
    ("httpx", "httpx[socks]"),
    ("Crypto", "pycryptodome"),
    ("aiohttp", "aiohttp"),
    ("google_play_scraper", "google-play-scraper"),
    ("protobuf_decoder", "protobuf-decoder"),
    ("google.protobuf", "protobuf>=6,<8"),
    ("jwt", "PyJWT"),
    ("requests", "requests"),
    ("urllib3", "urllib3"),
)


def check_dependencies() -> Tuple[bool, list]:
    results = []
    ok = True
    for module_name, package in DEPENDENCIES:
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", "")
            results.append((True, package, version))
        except Exception as exc:  # pragma: no cover - depends on environment
            ok = False
            results.append((False, package, f"{type(exc).__name__}: {exc}"))

    # The generated protobuf modules need protobuf 6.x or 7.x.
    try:
        import google.protobuf

        major = int(str(google.protobuf.__version__).split(".", 1)[0])
        if not (6 <= major < 8):
            ok = False
            results.append((False, "protobuf major version", google.protobuf.__version__))
    except Exception:
        pass

    # The dashboard is aiohttp; fastapi is not used by SahilOps.  Report it as
    # optional rather than failing a correct installation.
    try:
        import fastapi

        results.append((True, "fastapi (optional)", getattr(fastapi, "__version__", "")))
    except Exception:
        results.append((True, "fastapi (optional, not required)", "not installed"))

    return ok, results


def verify_database() -> dict:
    """Create/upgrade the SQLite schema and verify its shape."""
    from core import storage

    counts = storage.init()
    db_path = Path(storage.DB_PATH).resolve()
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_tables = {"accounts", "devices", "tokens", "kv"}
        missing_tables = sorted(required_tables - tables)

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(accounts)")
        }
        required_columns = {
            "id", "uid", "token", "data", "status", "error_count",
            "status_reason", "proxy", "updated_at",
        }
        missing_columns = sorted(required_columns - columns)

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    return {
        "path": str(db_path),
        "counts": counts,
        "tables": sorted(tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "integrity": integrity,
        "journal": journal,
        "ok": not missing_tables and not missing_columns and integrity == "ok",
    }


def verify_accounts() -> list:
    from core import storage

    return storage.list_accounts()


def port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


# ─────────────────────────── interaction ───────────────────────────

def print_region_menu(output: Callable[[str], None] = print) -> None:
    output("Select a region:")
    for index, region in enumerate(REGIONS, 1):
        note = "" if region.display == region.protocol else f"  (protocol: {region.protocol})"
        output(f"  {index}) {region.label}{note}")
    output("")


def resolve_region(value: str) -> Region:
    needle = str(value).strip().upper().replace("-", "").replace(" ", "")
    for region in REGIONS:
        if needle in region.aliases:
            return region
    raise SystemExit(
        f"Unknown region '{value}'. Choose one of: "
        + ", ".join(f"{r.display}/{r.protocol}" for r in REGIONS)
    )


def choose_region(input_fn: Callable[[str], str] = input,
                  output: Callable[[str], None] = print) -> Region:
    print_region_menu(output)
    default = REGIONS[0]
    while True:
        try:
            raw = input_fn(f"Region [1-{len(REGIONS)}] (default 1 - {default.label}): ").strip()
        except (EOFError, KeyboardInterrupt):
            output("")
            return default
        if not raw:
            return default
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(REGIONS):
                return REGIONS[index - 1]
        else:
            try:
                return resolve_region(raw)
            except SystemExit:
                pass
        output(f"  ✗ Invalid choice '{raw}'. Enter a number 1-{len(REGIONS)} or a region code.")


def prompt_int(prompt: str, default: int, min_v: int = 1, max_v: int = 10 ** 9,
               input_fn: Callable[[str], str] = input) -> int:
    while True:
        try:
            raw = input_fn(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print(f"  ✗ Enter a number between {min_v} and {max_v}")
            continue
        if min_v <= value <= max_v:
            return value
        print(f"  ✗ Enter a number between {min_v} and {max_v}")


def prompt_float(prompt: str, default: float, min_v: float = 0.0,
                 input_fn: Callable[[str], str] = input) -> float:
    while True:
        try:
            raw = input_fn(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            print(f"  ✗ Enter a number >= {min_v}")
            continue
        if value >= min_v:
            return value
        print(f"  ✗ Enter a number >= {min_v}")


def confirm(prompt: str, default: bool = True,
            input_fn: Callable[[str], str] = input) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        raw = input_fn(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


# ─────────────────────────── reporting ───────────────────────────

def print_banner() -> None:
    print("=" * 68)
    print("  SahilOps — unified workflow launcher")
    print("=" * 68)


def print_checks(env_source: Optional[Path], env_keys: Sequence[str],
                 venv_ok: bool, venv_detail: str,
                 deps_ok: bool, deps: Iterable[Tuple[bool, str, str]],
                 db: dict) -> None:
    source = str(env_source) if env_source else "none (process environment only)"
    print(f"[env]   loaded: {source}")
    if env_keys:
        print(f"        keys: {', '.join(env_keys)}")
    print(f"[venv]  {'OK' if venv_ok else 'WARN'} — {venv_detail}")

    missing = [package for ok, package, _detail in deps if not ok]
    if deps_ok:
        print("[deps]  OK — all runtime dependencies import cleanly")
    else:
        print(f"[deps]  FAIL — missing/broken: {', '.join(missing)}")
        print("        fix: .venv/bin/pip install -r requirements.txt")

    status = "OK" if db["ok"] else "FAIL"
    print(f"[db]    {status} — {db['path']}")
    print(f"        tables={', '.join(db['tables'])} journal={db['journal']} "
          f"integrity={db['integrity']}")
    print(f"        accounts={db['counts']['accounts']} "
          f"devices={db['counts']['devices']} tokens={db['counts']['tokens']}")
    if db["missing_tables"] or db["missing_columns"]:
        print(f"        missing tables={db['missing_tables']} "
              f"missing columns={db['missing_columns']}")


def print_accounts(accounts: Sequence[dict]) -> None:
    if not accounts:
        print("[accounts] none in the database")
        return
    print(f"[accounts] {len(accounts)} persisted:")
    for account in accounts[:20]:
        uid = str(account.get("uid") or "?")
        nickname = str(account.get("nickname") or "?")
        region = str(account.get("region_label") or account.get("region") or "?")
        status = str(account.get("status") or "active")
        print(f"  - uid={uid:<22} nickname={nickname:<24} region={region:<4} status={status}")
    if len(accounts) > 20:
        print(f"  ... and {len(accounts) - 20} more")


# ─────────────────────────── server ───────────────────────────

def start_server(host: str, port: int) -> None:
    import asyncio

    import core.app as app

    app.WEB_HOST = host
    app.WEB_PORT = port
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::1", "::") else host
    print()
    print(f"[serve] starting automation server on http://{shown}:{port} (bind {host})")
    print("[serve] Ctrl+C stops the bot and the dashboard cleanly")
    try:
        asyncio.run(app.main())
    except KeyboardInterrupt:
        print("\n[serve] interrupted")


# ─────────────────────────── main ───────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SahilOps unified workflow: env + DB + region + sequential generation + dashboard.",
    )
    parser.add_argument("--env", help="path to an env file (default: .env, then .env.example)")
    parser.add_argument("--check", action="store_true",
                        help="verify environment, dependencies, and database, then exit")
    parser.add_argument("--region", help="region code or name (IN/IND, ID, SG, TH, MY, VN, PH, BR, PK)")
    parser.add_argument("--accounts", type=int, help="number of guest accounts to generate")
    parser.add_argument("--delay", type=float,
                        help="seconds between sequential account generations (default: 8)")
    parser.add_argument("--host", help="dashboard bind address (default: SAHILOPS_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"dashboard port (default: {DEFAULT_PORT})")
    parser.add_argument("--no-generate", action="store_true",
                        help="skip guest generation and use accounts already in the database")
    parser.add_argument("--no-serve", action="store_true",
                        help="generate and verify only; do not start the server")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the plan without network generation or server startup")
    parser.add_argument("--allow-empty", action="store_true",
                        help="start the dashboard even when the database has no accounts")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="skip the confirmation prompt before live generation")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    print_banner()

    env_source, env_keys = load_environment(args.env)
    venv_ok, venv_detail = check_virtualenv()
    deps_ok, deps = check_dependencies()
    db = verify_database()
    print_checks(env_source, env_keys, venv_ok, venv_detail, deps_ok, deps, db)

    if not deps_ok or not db["ok"]:
        print("\n[workflow] prerequisite checks failed; fix the items above and retry.")
        return 1

    if args.check:
        print("\n[workflow] checks passed.")
        return 0

    if args.region:
        region = resolve_region(args.region)
        print(f"[region] {region.label} (protocol: {region.protocol})")
    else:
        region = choose_region()

    if args.accounts is not None:
        total = args.accounts
    else:
        total = prompt_int(f"Accounts to generate [{DEFAULT_ACCOUNTS}]: ", DEFAULT_ACCOUNTS, 0)
    if args.delay is not None:
        delay = args.delay
    else:
        delay = prompt_float(f"Delay between accounts in seconds [{DEFAULT_DELAY:g}]: ", DEFAULT_DELAY, 0.0)

    print()
    print(f"[plan] region={region.label} protocol={region.protocol} "
          f"accounts={total} delay={delay:g}s sequential=yes")

    if args.dry_run:
        print("[dry-run] skipping live generation and server startup")
        return 0

    counters = {"success": 0, "failed": 0, "rate_limited": 0}
    if args.no_generate or total == 0:
        print("[generate] skipped")
    else:
        if not args.yes and not confirm("Start live guest generation now?"):
            print("[generate] cancelled")
            return 0
        from utils import SaMIGEN

        print()
        print("[generate] sequential mode: one account at a time; no worker pool")
        try:
            counters = SaMIGEN.generate_accounts(
                total,
                region=region.protocol,
                region_label=region.display,
                delay=delay,
            )
        except KeyboardInterrupt:
            print("\n[generate] interrupted by user")
            return 130
        print()
        print(f"[generate] success={counters.get('success', 0)} "
              f"failed={counters.get('failed', 0)} "
              f"429_backoffs={counters.get('rate_limited', 0)}")

    accounts = verify_accounts()
    print()
    print_accounts(accounts)

    if not accounts and not args.allow_empty and not args.no_serve:
        print("\n[workflow] no accounts in the database; refusing to start the automation server.")
        print("           add accounts from the dashboard or rerun with --allow-empty.")
        return 1

    if args.no_serve:
        print("\n[workflow] generation/verification complete; server not started (--no-serve).")
        ok = bool(accounts) or args.allow_empty or args.no_generate
        return 0 if ok else 1

    host = args.host or os.environ.get("SAHILOPS_HOST", DEFAULT_HOST)
    if not port_available(host, args.port):
        print(f"\n[serve] port {args.port} is already in use on {host}; stop the other process or use --port.")
        return 1

    start_server(host, args.port)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[workflow] interrupted")
        raise SystemExit(130)
