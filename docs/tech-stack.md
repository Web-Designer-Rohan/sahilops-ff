# Tech Stack

## Runtime

- **Python 3.10+** (asyncio, f-strings, `asyncio.to_thread`). Shipped `.pyc`
  files are for 3.13 but are not required.
- Entry points: `python -m core.app`, `python utils/SaMIGEN.py`, `python utils/join_guild.py`.

## Libraries

| Library | Used for | Where | Pin |
|---|---|---|---|
| `asyncio` (stdlib) | bot loops, TCP/UDP clients, dashboard | core/app.py | — |
| `aiohttp` | dashboard web server | dashboard/dashboard_server.py | unpinned |
| `httpx` | async HTTP to Garena/login servers | core/app.py | unpinned |
| `requests` | sync HTTP (dashboard guild calls, generator, joiner) | dashboard/dashboard_server.py, utils/SaMIGEN.py, utils/join_guild.py | unpinned |
| `protobuf` | generated message classes | `core/*_pb2.py` | `>=6,<8` |
| `protobuf-decoder` | generic protobuf sniffing (`Parser`) | core/app.py | unpinned |
| `pycryptodome` (`Crypto`) | AES-CBC payload encryption | all scripts | unpinned |
| `google-play-scraper` | fetch current Free Fire version from Play Store | core/app.py | unpinned |
| `PyJWT` | imported by dashboard, **never used** | dashboard/dashboard_server.py | unpinned |

`requirements.txt` lists `httpx`, `google-play-scraper`, `pycryptodome`,
`protobuf-decoder`, `protobuf>=6,<8`, `requests`, `aiohttp`, `PyJWT`, and
`urllib3`. The protobuf range is the only version constraint needed for the
shipped generated code.

**Verified dependency bug:** `core/thunderFF_pb2.py` and `core/data_pb2.py` import `google.protobuf.runtime_version`
(protobuf ≥ 5.27) and were generated with protobuf 7.35.1 / 6.33.1.
`protobuf==4.25.3` fails at import (no `runtime_version`), and `5.29.5` fails on
`data_pb2` ("mismatched Gencode/Runtime major versions"). `6.33.1` and `7.36.2`
work — so the practical floor is **protobuf 6**.

## Protocols and crypto

| Piece | Detail | Location |
|---|---|---|
| Login payload cipher | AES-128-CBC, PKCS#7, fixed key/IV | core/app.py |
| TCP gateway frame | 2-byte msg key + CRC7 + XOR-obfuscated header, tea-CBC body | core/app.py |
| TEA | 16 rounds, delta `0x9E3779B9`, custom CBC | core/app.py |
| CRC7 | 256-byte lookup table | core/app.py |
| UDP match frame | same header scheme, `sv_frame`/`build_packet` | core/app.py |
| Protobuf | hand-built varint/length encoding + generated `_pb2` parsers | core/app.py |
| JWT split | login token split at **character 660** into `thunder` / `sharma` | core/app.py |

## External services

| Service | Reason |
|---|---|
| `100067.connect.garena.com` | OAuth guest register / token grant / token inspect |
| `loginbp.ppmainecoonghj.com` | `MajorLogin`, `GetLoginData`, `Ping`, `ChooseNewbieChoice` |
| `version.ggwhitehawk.com` | remote version + server URL lookup |
| `client*.freefiremobile.com`, `clientbp.*` | game gateway, clan, friend APIs |
| Google Play | version scrape |
| `danger-guild-management-web.vercel.app` (joiner) | third-party guild join/leave backend |
| `ags-community-jwt-api.vercel.app` (joiner) | third-party JWT provider |

## Secrets and data files

- `token_cache.json` — 7 cached accounts with JWT, `access_token`, AES key/IV.
- `devices.json` — per-account fake device profile, including client IP.
- Hard-coded `CLIENT_SECRET` (`utils/SaMIGEN.py`) and a dashboard API key
  `SAHILOPS` (`dashboard/dashboard_server.py`, `dashboard/index.html`).

Version strings diverge across files (`OB54` vs `OB55`, `1.130.22`, `1.132.6`,
`1.132.9`, `2.133.9`), so a game update usually requires several edits.
