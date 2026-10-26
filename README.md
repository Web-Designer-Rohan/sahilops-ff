# SahilOps

SahilOps is a Free Fire automation workspace for account login, squad matchmaking,
Battle Royale match play, account generation, and guild utilities. The dashboard
and core engine run from one Python process.

## Layout

```text
core/       Core bot engine, protocol helpers, protobuf modules
dashboard/  aiohttp dashboard server and web UI
utils/      Account generator, guild joiner, decoders, helpers
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
storage layer, API-key enforcement on every write route, secret redaction,
account add/pause/restart/delete, live log streaming, squad sizing, and the
region-override patch:

```bash
.venv/bin/python tests/smoke_test.py
```

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
- Automating live game accounts can violate the game's terms of service.
