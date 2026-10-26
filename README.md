# SahilOps

SahilOps is a Free Fire automation workspace for account login, squad matchmaking,
Battle Royale match play, account generation, and guild utilities. The dashboard
and core engine run from one Python process.

## Layout

```text
core/       Core bot engine, protocol helpers, protobuf modules
dashboard/  aiohttp dashboard server and web UI
utils/      Account generator, guild joiner, decoders, helpers
tests/      Smoke test suite (storage, dashboard, health, proxies, alerts)
docs/       Setup, architecture, audit, and upgrade notes
archive/    Original source archives kept for reference
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m core.app
```

Open `http://localhost:20333` for the dashboard. It binds `127.0.0.1` by
default; set `SAHILOPS_HOST=0.0.0.0` if you need access from another machine.

Optional settings (API key, webhook, proxies, health threshold) are documented
in `.env.example`; export them or load that file before starting the bot.

Accounts can be added from the dashboard, or placed in a legacy `accounts.json`
that is imported once on first start:

```json
[{"uid": "123456789", "password": "your-password"}]
```

## State storage

Accounts, device fingerprints, and cached sessions live in `sahilops.db`
(SQLite, WAL mode) instead of separate JSON files. The dashboard, the account
generator, and the bot engine all go through `core/storage.py`, so they can no
longer overwrite each other's writes. On first start the database imports
`accounts.json`, `devices.json`, and `token_cache.json` once, then leaves them
alone; those JSON files are no longer written.

Set `SAHILOPS_DB=/path/to/file.db` to use a different database location.

## Notifications

Set `SAHILOPS_WEBHOOK_URL` to a Discord webhook URL, or to a Telegram bot
`sendMessage` URL together with `SAHILOPS_TELEGRAM_CHAT_ID`. With no URL the
notifier is a no-op.

Alerts are sent for level-ups, completed matches, critical session errors,
suspended/quarantined accounts, and squad-formation failures. They are queued
and delivered by one background worker, so a slow endpoint never blocks a
match or shutdown; token-shaped strings are scrubbed before sending. Session
and squad alerts are throttled per account. See `.env.example` for every
setting.

## Account health

Each account has a health status in SQLite:

- `active` — normal operation.
- `suspended` — a login/gateway response mentioned a ban or restriction phrase
  or code (`SAHILOPS_BAN_CODES`). The worker stops and an alert is sent.
- `quarantined` — the account hit `SAHILOPS_ERROR_THRESHOLD` consecutive
  failures (default 5). The worker stops instead of retrying a dead credential.

A successful TCP connection resets the error counter. The dashboard's Account
Health card shows the status, error count, reason, and proxy per account.
Restart (or re-add) is the explicit retry: it resets the account to `active`.

## Proxies

Accounts can carry their own proxy string in SQLite — pass an optional proxy
when adding an account from the dashboard, accepted as `socks5://`, `socks5h://`,
`http://`, or `https://`, optionally with `user:pass@`. Login, session,
gateway TCP, and ping traffic for that account are routed through it
(`socks5h` resolves DNS at the proxy); credentials are masked everywhere they
are displayed. Match UDP traffic is not tunnelled.

## Dashboard security

Every state-changing route (`POST /api/account/*`, `POST /api/logs/clear`,
`POST /api/clanjoin`) requires the control-panel key in the `X-API-Key` header;
requests without it get `401`. `/api/guildinfo` accepts the same header (the
legacy `?key=` form still works for scripts).

The page served by the dashboard gets the key from the server at load time, so
nothing is hard-coded in the HTML. The default key is `SAHILOPS`; set
`SAHILOPS_API_KEY` to change it — do that before exposing the panel with
`SAHILOPS_HOST=0.0.0.0`.

`/api/stats` and `/api/logs` run every response through `redact_secrets()`:
secret fields (`token`, `access_token`, `refresh_token`, `password`, `secret`,
`api_key`, AES key/IV, session keys, credentials, cookies, `Authorization`) are
dropped recursively, and JWT / `Bearer …` / `token=…` shapes are scrubbed from
log text. Session data therefore never reaches the browser.

## Tests

The smoke test starts the real dashboard on `127.0.0.1:20333` and checks the
storage layer (including the schema upgrade and health transitions), API-key
enforcement on every write route, secret redaction, account
add/pause/restart/delete, live log streaming, account health annotations,
HTTP/SOCKS5h proxy tunnels against local fake proxies, webhook payloads and
delivery, squad sizing, and the region-override patch:

```bash
.venv/bin/python tests/smoke_test.py   # 102/102 checks
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request for Python
3.10–3.12: it installs `requirements.txt`, compiles `core`, `dashboard`, and
`utils` with `compileall`, then runs the smoke suite.

## Utilities

```bash
python utils/SaMIGEN.py        # bulk guest-account generator
python utils/join_guild.py     # guild join/leave and owner request tool
python utils/decode.py         # packet inspector
python utils/encode-id-clan.py # clan-id encoder
```

## Notes

- Python 3.10+ is required.
- Generated protobuf modules need `protobuf>=6,<8`.
- `sahilops.db`, `token_cache.json`, `devices.json`, and `accounts.json` are
  runtime data and are excluded from version control by default.
- SOCKS proxies in HTTP calls need `httpx[socks]`, which `requirements.txt`
  installs.
- Automating live game accounts can violate the game's terms of service.
