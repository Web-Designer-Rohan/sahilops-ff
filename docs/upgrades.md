# Upgrades

Ordered by impact ÷ effort. "Effort" is rough (S ≤ ½ day, M ≤ 2 days, L > 2 days).

> **SahilOps status:** P0 #3–#8 are implemented. #1 is partial (`.gitignore`
> added; rotating the leaked tokens is still an operator step) and #2 is
> partial (the dashboard now binds `127.0.0.1`; auth middleware and API secret
> filtering remain recommendations).

## P0 — do first (security & correctness)

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | Revoke every token in `token_cache.json`; remove caches from distributions; add `.gitignore` | Live sessions and device IDs are already leaked | S |
| 2 | Bind dashboard to `127.0.0.1`, add token auth middleware, remove account secrets from API responses | Anyone on the LAN can currently control the bot | S |
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
| 13 | Cap retries with exponential backoff + jitter; surface a permanent-failure state | Infinite 15 s login loops and generator hangs | S |
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

## Known limitations

- **Battle Royale only.** `mode`/`LONE_WOLF` UI is vestigial.
- **One process, many accounts.** No horizontal scaling; squad state is in memory.
- **Protocol constants are reverse-engineered guesses** (TEA layout, 660-char JWT
  split, movement packet tail bytes). A game patch can break them at any time.
- **No account-level rate limiting.** Many accounts share one outbound IP and
  fixed device/version headers.
- **No test or CI setup.** `__pycache__` from 3.13 is shipped but unused.
- **Ethics/ToS:** the tool automates live game accounts; using it can get accounts
  banned and violates the game's terms.

## Suggested order of work

1. Rotate secrets + lock down the dashboard (hours, no protocol knowledge needed).
2. Fix the four UI/logic lies: region override, pause, restart, single-account queue.
3. Add tests around pure encode/decode helpers before refactoring.
4. Centralise config and only then touch protocol code.
