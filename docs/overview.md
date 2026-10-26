# Overview

## Purpose

SahilOps is a private Free Fire (Android package `com.dts.freefireth`)
automation workspace. It does three separate jobs:

| Program | Job |
|---|---|
| `core/app.py` | Logs in multiple guest/token accounts, forms a squad, queues Battle Royale, plays the UDP match, and reports EXP/guild progress on a web dashboard |
| `utils/SaMIGEN.py` | Bulk-creates new guest accounts and appends them to `accounts.json` |
| `utils/join_guild.py` | Makes accounts join/leave a guild and sends friend requests to the guild owner |

## Key features

- **Two login modes** — guest UID + password, or a Garena access token.
- **Session cache** — `token_cache.json` avoids re-login for 75 minutes
  (`TOKEN_CACHE_TTL = 4500`, `core/app.py`).
- **Device fingerprint per account** — random phone model/GPU/IP stored in
  `devices.json` (`core/app.py`).
- **Flexible squad automation** — one account becomes leader, creates a squad
  sized to the active peers, invites the others, and everyone sends
  `ready` + `start match`. Solo, duo, trio, and full four-player squads work.
- **Battle Royale only** — mode/map IDs are fixed to Battle Royale
  (`BR_MODE_ID = 1`, `BR_MAP_ID = 1`, `core/app.py`).
- **UDP match client** — custom OICQ/tea-CBC framing, HELLO/ACK/PING replies,
  anti-AFK fire packets, and movement routines (`core/app.py`).
- **Web dashboard** — `http://localhost:20333` with guild XP, uptime, live logs,
  add-account, and pause/resume controls.
- **EXP tracking** — polls `GetLoginData` every ~30 s and computes level
  progress from the built-in EXP table (`dashboard/dashboard_server.py`).
- **Guild tools** — HTTP APIs for guild XP and mass clan-join using cached
  tokens (`dashboard/dashboard_server.py`).

## Current fixes

- Match-search packets use `thunderFF_pb2.StartMatch`, so the selected region
  and remote version are actually applied.
- Pause closes registered TCP writers, waits while paused, and reconnects on
  resume.
- Squad target size adapts to the number of active account workers instead of
  requiring exactly four accounts.

## What it is not

- No test suite or CI yet.
- Settings are constants in `core/app.py`; there is no config file.
- The dashboard is intended for a trusted local network and has no real
  authentication layer.
- Automating live game accounts can get accounts banned and violates the
  game's terms of service.
