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

Open `http://localhost:20333` for the dashboard.

Accounts can be placed in `accounts.json`:

```json
[{"uid": "123456789", "password": "your-password"}]
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
- `token_cache.json`, `devices.json`, and `accounts.json` are runtime data and
  are excluded from version control by default.
- Automating live game accounts can violate the game's terms of service.
