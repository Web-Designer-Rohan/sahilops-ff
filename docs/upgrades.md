# Upgrades

Ordered by impact ÷ effort. "Effort" is rough (S ≤ ½ day, M ≤ 2 days, L > 2 days).

> **SahilOps status:** P0 #2–#8 are implemented. #1 is partial (`.gitignore`
> added; rotating the leaked tokens is still an operator step) and #2 is
> complete (localhost binding, API-key middleware on every write route, and
> secret filtering on `/api/stats` and `/api/logs`). In P2, #18 (SQLite state)
> and #19 (background-task draining) are complete; #13 (bounded retries with a
> permanent-failure state) is now covered by health quarantine. The latest
> milestone adds webhook alerts, per-account proxies, and CI — see
> [P2 storage & testing milestone](#p2-storage--testing-milestone),
> [P0 #2 milestone](#p0-2-milestone-dashboard-auth--secret-filtering), and
> [P3 notifications, health & CI milestone](#p3-notifications-health--ci-milestone).

## P0 — do first (security & correctness)

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | Revoke every token in `token_cache.json`; remove caches from distributions; add `.gitignore` | Live sessions and device IDs are already leaked | S |
| 2 | ~~Bind dashboard to `127.0.0.1`, add token auth middleware, remove account secrets from API responses~~ — **done** | Anyone on the LAN could control the bot | S |
| 3 | Delete `sendAutomaticJoinRequest()` from page load; make clan join an explicit confirmed button | Opening the dashboard silently joins a third-party clan with all accounts | S |
| 4 | Replace `CguFF_pb2` with `thunderFF_pb2.StartMatch` (or import `StartMatch_pb2`) | Region/version currently ignored in every search | S |
| 5 | Wire `register_writer`/`unregister_writer` and implement a real pause/resume | Stop/Restart buttons lie; paused accounts keep their sockets | M |
| 6 | Make `/api/account/restart` resume workers and return real state | Current button is a no-op | S |
| 7 | Auto-disable squad mode below 4 accounts (or allow solo search) | Default single-account use never queues | S |
| 8 | Pin dependencies (`protobuf>=6,<8`, others exact) and add `urllib3` | Fresh installs can fail at import | S |

## P1 — reliability & maintainability

| # | Change | Why | Effort |
|---|---|---|---|
| 9 | Central `config.py` (versions, regions, timeouts, feature flags) | Six different version strings cause partial breakage after updates | M |
| 10 | Replace guessed packet parsing with schema-based parsing where possible; log+drop unparsable packets | Heuristics can accept the wrong invite/metrics | M |
| 11 | Implement `relogin_account` (or delete the branch) | Cache expiry currently forces a full restart | S |
| 12 | Run sync HTTP in `asyncio.to_thread` or `httpx.AsyncClient` in the dashboard | Dashboard freezes up to 15 s per token | S |
| 13 | ~~Cap retries with exponential backoff + jitter; surface a permanent-failure state~~ — **done** via health quarantine | Infinite 15 s login loops and generator hangs | S |
| 14 | Add pytest tests for pure functions: `calculate_level_progress`, `create_proto`, `build_proto`, `crc7_buff`, `tea_*`, `decode_packet` round-trip, dashboard handlers | Zero tests today; these are cheap and catch real regressions | M |
| 15 | Return structured JSON logs; render with `textContent` | Removes HTML-injection path and enables filtering | S |

## P2 — product & scale

| # | Change | Why | Effort |
|---|---|---|---|
| 16 | Render the account list already returned by `/api/stats` (level, EXP, status, matches, pause/restart per account) | The dashboard advertises account tracking but shows none | M |
| 17 | Replace global `_team_state` with a per-account/session object | Shared globals couple all accounts and cause leader races | M |
| 18 | Persist state (SQLite) instead of JSON files | Avoids lost updates between dashboard, generator and app | M |
| 19 | Drain/await background tasks on shutdown; remove bare `except Exception: pass` around critical sends | Silent failures make debugging impossible | M |
| 20 | Package as a proper project (`pyproject.toml`, `README`, `--dry-run`, `--log-level`) | Reproducible installs and safer operation | M |
| 21 | Drop the joiner's third-party backends; self-host with POST + TLS verification | Credential exfiltration risk | L |

## P2 storage & testing milestone

Status: complete. Closed items **#18** and **#19**.

### #18 — SQLite state instead of JSON

`core/storage.py` is now the single write path for accounts, device
fingerprints, and cached sessions (`sahilops.db`, WAL mode, one connection per
process, serialized writes).

- `core/app.py`, `dashboard/dashboard_server.py`, and `utils/SaMIGEN.py` no
  longer read or write `accounts.json`, `devices.json`, or `token_cache.json`.
- The three legacy JSON files are imported once on first start (guarded by a
  `kv` marker) and then left untouched.
- Adding, deleting, or restarting an account from the dashboard updates the
  same rows the engine loads at startup, so the generator and the dashboard can
  run concurrently without lost updates.
- Override the location with `SAHILOPS_DB`; the database file is git-ignored and
  created with mode `0600`.

### #19 — Background tasks and shutdown

- `BotState.spawn()` tracks every fire-and-forget callback task; failures are
  logged instead of being swallowed.
- `BotState.drain()` awaits pending tasks with a timeout, then cancels what is
  left, so shutdown cannot hang on a stuck callback.
- `main()` now always runs its cleanup path: workers are cancelled and awaited,
  background tasks are drained, the aiohttp runner is cleaned up (port
  released), and the database is closed.
- Silent `except Exception: pass` blocks around handshake sends (cmd-101 reply,
  HELLO resend, startup packet, keepalive, friend confirm) now log a warning.
- Region override is a testable pure helper, `apply_region_override()`, used by
  `start_game_battle_royale()`; on a parse failure it returns the original
  packet rather than a partially mutated one.

### Verification

```bash
python -m compileall -q core dashboard utils   # clean
.venv/bin/python tests/smoke_test.py           # 52/52 checks
python -m core.app                             # binds 127.0.0.1:20333
```

The smoke test covers the storage layer (including 160 concurrent upserts and
JSON migration), dashboard index/stats/logs, account add → pause → restart →
delete cycles, callback draining, squad sizing (`_squad_target_size` clamps to
`[1, TEAM_SIZE]`), and region override for `ID`/`SG`/`IND`. The live run was
verified to bind only `127.0.0.1:20333`, serve `/api/stats` and `/api/logs`, and
release the port on SIGINT with `Closed.` in the log.

## P0 #2 milestone: dashboard auth & secret filtering

Status: complete.

- An aiohttp middleware (`api_key_middleware`) rejects every `POST`/`PUT`/
  `PATCH`/`DELETE` that does not carry `X-API-Key` matching `SAHILOPS_API_KEY`
  (default `SAHILOPS`), compared with `hmac.compare_digest`. All account add /
  delete / refresh / restart / pause / pause-all routes, log clearing, and clan
  join are covered, so a forgotten handler cannot be written around.
- `/api/guildinfo` validates the same header (legacy `?key=` still accepted);
  the key is no longer hard-coded in URLs or the page. `handle_index` injects
  the configured key into the served HTML.
- `redact_secrets()` removes secret fields recursively (`token`, `access_token`,
  `refresh_token`, `password`, `secret`, `api_key`, AES key/IV, session keys,
  credentials, cookies, `Authorization`) from `/api/stats`, `/api/logs`, and the
  delete response, and scrubs JWT / `Bearer …` / `key=value` shapes from text.
- `/api/stats` builds copies of account dicts before annotating them, so a read
  path no longer mutates live state.

Verification: `tests/smoke_test.py` now exercises all eight write routes without
and with a key, the redaction helpers, and the live responses (52/52 checks).

## P3 notifications, health & CI milestone

Status: complete. Closes #13 (bounded retries plus a permanent-failure state)
and adds the operational pieces the security/scale work needs.

### Webhook notifications (`core/notifications.py`)

- `SAHILOPS_WEBHOOK_URL` accepts a Discord webhook URL or a Telegram bot
  `sendMessage` URL (`SAHILOPS_TELEGRAM_CHAT_ID` required for Telegram).
- Alerts are queued and sent by one background worker with a bounded queue, so
  a slow or dead endpoint cannot block a match, a login, or shutdown. Failures
  are retried once and recorded; the bot keeps running.
- Message text is scrubbed of JWT / `Bearer …` / `key=value` shapes before it
  leaves the process.
- Events: level-up (`refresh_account_profile`), match completion (`play_game`),
  suspension/quarantine, exhausted reconnect attempts, and squad-formation
  failure. Team/session events are throttled to one alert per 5 minutes per
  account.
- `main()` flushes the queue and stops the worker during shutdown.

### Account health and quarantine (`core/health.py`, `core/storage.py`)

- `accounts` gained `status` (`active` / `suspended` / `quarantined`),
  `error_count`, `status_reason`, and `proxy` columns. Older databases are
  altered in place on open, so no manual migration is needed.
- Login and gateway responses (OAuth JSON, `MajorLogin`, `GetLoginData`, and
  gateway exceptions) are scanned for ban/restriction phrases and codes
  (`SAHILOPS_BAN_CODES`). A positive match marks the account `suspended`, pauses
  it, and alerts; the worker exits instead of retrying a dead credential.
- Other failures increment `error_count`; reaching `SAHILOPS_ERROR_THRESHOLD`
  (default 5) marks the account `quarantined` and stops the worker. A successful
  TCP connection resets the counter.
- `_start_worker()` refuses to start workers for suspended/quarantined records.
  The dashboard restart button reactivates the record, which is the explicit
  retry path; re-adding an account also resets it to `active`.
- `/api/stats` annotates each account with `health: {status, error_count,
  reason, proxy}`, and the dashboard shows a badge per account plus a flagged
  count. Proxy credentials are masked in every response.

### Per-account proxies (`core/proxy.py`)

- Accounts can carry a proxy (`http://`, `https://`, `socks5://`, `socks5h://`)
  in SQLite; the dashboard add-account form accepts an optional proxy and
  rejects unknown schemes.
- HTTP calls (OAuth, `MajorLogin`, `GetLoginData`, ping) use a cached
  per-proxy `httpx` client. Game gateway TCP connections are tunnelled through
  the same proxy with a CONNECT or SOCKS5 handshake; DNS is left to the proxy
  (no local resolution when a proxy is set).
- `socks5://` resolves locally, `socks5h://` resolves at the proxy. The match
  UDP socket is not tunnelled; it still leaves from the host's address.

### CI (`.github/workflows/ci.yml`)

- Runs on every push and pull request across Python 3.10–3.12: checkout, pip
  install, `python -m compileall -q core dashboard utils`, then
  `python tests/smoke_test.py`.
- `requirements.txt` now installs `httpx[socks]` so SOCKS proxies work.

### Verification

```bash
python -m compileall -q core dashboard utils   # clean
.venv/bin/python tests/smoke_test.py           # 102/102 checks
```

The smoke test now also covers the schema upgrade on an old database, status
and error-count transitions, suspension phrase/code detection (including false
positives), HTTP and SOCKS5h tunnel round-trips against local fake proxies,
webhook payload shapes and delivery/redaction, `/api/stats` health annotations,
and proxy validation on the add-account route.

## Known limitations

- **Battle Royale only.** `mode`/`LONE_WOLF` UI is vestigial.
- **One process, many accounts.** No horizontal scaling; squad state is in memory.
- **The squad bot still has no protocol test.** The smoke test covers storage,
  the dashboard, health, proxies, alerts, squad sizing, and region override;
  live match flow needs real accounts and game servers.
- **Protocol constants are reverse-engineered guesses** (TEA layout, 660-char JWT
  split, movement packet tail bytes). A game patch can break them at any time.
- **No account-level rate limiting.** Many accounts share one outbound IP and
  fixed device/version headers.
- **Match UDP traffic is not proxied.** TCP login, session, and ping requests
  use the per-account proxy; the Battle Royale UDP socket still leaves from the
  host's address.
- **Ban detection is keyword/code based.** It cannot know a new restriction
  format until its phrase or code is added to `core/health.py` /
  `SAHILOPS_BAN_CODES`.
- **Quarantine requires an explicit restart.** A quarantined account stays
  stopped until the dashboard restart (or a re-add) clears it.
- **Alerts are best-effort.** A dead webhook retries once, then drops the
  message; nothing is persisted for later delivery.
- **The dashboard key is a local access gate, not multi-user auth.** The page
  receives the key from the server, so anyone who can load it can send writes.
- **Ethics/ToS:** the tool automates live game accounts; using it can get accounts
  banned and violates the game's terms.

## Suggested order of work

1. Rotate secrets + lock down the dashboard (hours, no protocol knowledge needed).
2. Fix the four UI/logic lies: region override, pause, restart, single-account queue.
3. Add tests around pure encode/decode helpers before refactoring.
4. Centralise config and only then touch protocol code.
