# Bugs & Mistakes

Legend: **C** = confirmed by tracing and/or reproducing the code path; **R** = risk
(inferred, cannot be proven without the live game servers).

> **SahilOps status:** bugs #1 (region/version override), #2 (pause/resume
> sockets), #3 (restart), #4 (squads below four accounts), #5 (automatic clan
> join), #6 (network binding + control-panel auth + secret filtering), #13
> (fake API-key auth), and #18 (duplicate `handle_index`) are fixed in the
> current build. The table and notes below keep the original audit wording;
> paths now live under `core/` and `dashboard/`.

## Confirmed bugs

| # | Sev | Bug | Where |
|---|---|---|---|
| 1 | High | Match-search packet never gets the chosen region/version (undefined `CguFF_pb2`) | `core/app.py:832` |
| 2 | High | "Pause" neither disconnects nor really resumes | `dashboard/dashboard_server.py:256–293, 412–462` |
| 3 | High | "Restart Bot" button is a no-op | `dashboard/index.html:705–730`, `dashboard/dashboard_server.py:950–960` |
| 4 | High | Fewer than 4 accounts can never queue a match | `core/app.py:89–99, 1665–1751` |
| 5 | High | Dashboard auto-sends a clan-join for a hard-coded clan on page load | `dashboard/index.html:566–567, 732–751` |
| 6 | Critical | Dashboard has no auth and listens on `0.0.0.0`; anyone can add/delete/pause accounts | `core/app.py:28`, `dashboard/dashboard_server.py:1003–1016` |
| 7 | Critical | Real-looking sessions shipped in `token_cache.json` (+ `devices.json`) | archive root |
| 8 | Critical | Joiner sends UID/password in GET URLs to third-party servers | `utils/join_guild.py:124–158, 252–257` |
| 9 | High | Joiner writes plaintext passwords on failure | `utils/join_guild.py:341–346` |
| 10 | Medium | `relogin_account()` does not exist → cache-expiry path always fails | `core/app.py:1837` |
| 11 | Medium | Blocking `requests` calls inside async handlers freeze the dashboard | `dashboard/dashboard_server.py:78–113, 668` |
| 12 | Medium | Unpinned protobuf breaks the app on common versions | `requirements.txt:5`, `*_pb2.py` |
| 13 | Medium | Hard-coded API key in URL, duplicated in the HTML | `dashboard/dashboard_server.py:653`, `dashboard/index.html:580` |
| 14 | Medium | Version constants disagree across files | `core/app.py:204`, `utils/join_guild.py:224`, etc. |
| 15 | Low | Account "mode" never updates on the update path | `dashboard/dashboard_server.py:321–381` |
| 16 | Low | `total_matches_started` is always 0 | `dashboard/dashboard_server.py:539–541` |
| 17 | Low | Unescaped log HTML rendered with `innerHTML` | `dashboard/dashboard_server.py:987–998`, `dashboard/index.html:643` |
| 18 | Low | Duplicate `handle_index` definition (dead code) | `dashboard/dashboard_server.py:181, 567` |
| 19 | Low | `encode-id-clan.py` hard-codes a clan ID | `encode-id-clan.py:10` |

---

### 1. Region/version override silently does nothing (High)

`start_game_battle_royale()` parses the search packet with `CguFF_pb2.StartMatch()`,
but `CguFF_pb2` is **never imported** — the `NameError` is swallowed by
`except Exception: pass` (`core/app.py:839`), so the packet is sent unmodified.

**Verified:** sent a search with region `SG`, version `9.9.9`; decrypting the sent
body still shows regions `['EUROPE','EUROPE']` and version `1.130.22`
(hard-coded in `BR_HEX`, `core/app.py:798`).

**Impact:** region rotation (`REGION_ROTATION_LIST`) and version updates are
cosmetic; all searches use the baked-in packet.

**Status: fixed.** `core/app.py` uses `thunderFF_pb2.StartMatch()` and writes the
selected region into every `main.region_list` entry plus
`main.client_version.remote_version` before serialization.

### 2. Pause/resume is broken (High)

`toggle_pause()` claims to disconnect the TCP socket by calling
`close_writers_for_account()`, which iterates `active_writers`. `register_writer()`
is defined but **never called** anywhere in `app.py`, so the set is always empty.

**Verified:** after `toggle_pause("111")`, status is `PAUSED` and
`active_writers == {}` — no socket is closed.

Only the squad loops read `is_paused()`. A paused account keeps its gateway
connection; an in-flight match keeps playing. When paused, the coordinator loop
exits; there is no code that restarts it on resume. Recovery depends on the
gateway closing the socket or on the "team idle" timeout, which may never fire
because incoming keep-alive replies reset the counter (`core/app.py:1936`).

**Status: fixed.** `core/app.py` registers each connected writer with
`bot_state`, unregisters it in `finally`, and waits while `is_paused()` is true
so a paused account does not immediately reconnect.

### 3. "Restart Bot" does nothing (High)

`index.html` posts `{}` to `/api/account/restart`. The handler reads
`data["uid"]` → `""`, then calls `on_restart_account` (never registered in
`core/app.py:2472–2475`) or falls back to `on_refresh_account("")`, which returns
immediately because no account has that ID.

Meanwhile the UI flips `isAllPaused` and relabels the button, so the dashboard
now *claims* the bot is running while it is still paused. Reloading resets the
label to "Stop Bot".

**Status: fixed.** `core/app.py` registers `on_restart_account`, which clears
the pause flags, cancels the old worker tasks, and relaunches them from
`accounts.json`. `/api/account/restart` now returns `503` when the callback is
missing and `404` for an unknown UID instead of silently succeeding.

### 4. Under 4 accounts = no matchmaking (High)

`TEAM_SIZE = 4` and `TEAM_MODE = True`. The leader waits 90 s for peers, creates
a team, invites for 120 s, then aborts if `final_cnt < 4` (`core/app.py:1746`) and
returns. The `else` branch that sends a solo search packet
(`core/app.py:1912–1913`) is unreachable with the shipped config.

**Verified by code path:** one account never receives `ready`/`start` packets and
enters the read loop. After 1800 × 0.5 s with no incoming data it raises
"Team idle timeout" and reconnects; if the gateway answers keep-alives, that
counter keeps resetting and the account can idle indefinitely.

**Status: fixed.** The squad coordinator chooses a target from the active
peers (`1`–`4`), so solo, duo, and trio queues no longer wait forever for a
fourth account.

### 5. Dashboard triggers a clan join on every page load (High)

`DOMContentLoaded` calls `sendAutomaticJoinRequest()`, which POSTs the hard-coded
`DEFAULT_CLAN_ID = "3055517625"` to `/api/clanjoin`. That endpoint sends a join
request with **every** token in `token_cache.json`.

**Impact:** opening the dashboard enlists all accounts in someone else's clan
without warning. The clan card also displays that hard-coded ID.

**Status: fixed.** The automatic `sendAutomaticJoinRequest()` call and helper
were removed. Opening the dashboard now only reads stats and guild info; clan
join still requires an explicit `POST /api/clanjoin`.

### 6. Unauthenticated network control panel (Critical)

`WEB_HOST = "0.0.0.0"`, port `20333`, and no auth middleware on any route.
On the same network anyone can add/delete accounts, pause the bot, read logs,
read account metadata from `/api/stats`, and trigger clan joins with all cached
tokens. (The `token` field in account records is usually `""` because `app.py`
never passes it to `register_account`, but the control surface is still wide open.)

**Status: fixed.** `WEB_HOST` defaults to `127.0.0.1`, and an aiohttp middleware
now rejects every state-changing request (`POST`/`PUT`/`PATCH`/`DELETE`) that does
not carry the control-panel key in the `X-API-Key` header (constant-time compare).
That covers account add/delete/refresh/restart, pause, pause-all, log clearing,
and `/api/clanjoin`, so an unauthenticated caller can no longer mutate anything.
`/api/stats` and `/api/logs` strip secret fields (`token`, `access_token`,
`password`, AES keys/IV, session keys, credentials) recursively and scrub JWT
and `key=value` shapes from log text before serialising. Operators who expose
the panel with `SAHILOPS_HOST=0.0.0.0` should also set a non-default
`SAHILOPS_API_KEY`; the key is injected into the served page by the server.

### 7. Shipped session cache (Critical)

`token_cache.json` contains 7 accounts with an 822-character JWT, `access_token`,
`open_id`, AES key/IV and level/EXP. `devices.json` contains matching device IDs.
Anyone with the ZIP can reuse these sessions.

**Fix:** rotate/revoke all tokens, delete the cache from distributions, add
`token_cache.json`, `devices.json`, `accounts.json` and `generator.log` to
`.gitignore`.

### 8. Credentials in third-party URLs (Critical)

`join_guild.py` sends `?uid=…&password=…` (GET, TLS verification disabled) to
`danger-guild-management-web.vercel.app`, and forwards the same pair to
`ags-community-jwt-api.vercel.app` to obtain a JWT.

**Impact:** full account takeover by the operators of those services or anyone
who sees their logs.

**Fix:** self-host the backend, use POST bodies, enable certificate verification,
and never send raw passwords to a third party.

### 9. Plaintext failure log (High)

`record_failed_account()` appends `uid:password` to
`SahilOps/failed_accounts.txt`.

**Fix:** store UID + reason only, or encrypt the file.

### 10. Missing `relogin_account` (Medium)

The cache-miss branch calls `await bot_state.relogin_account(...)`
(`core/app.py:1837`), but `BotState` has no such method (`hasattr` → `False`).
The `AttributeError` is caught, logged as "Re-login failed", and converted to
`ConnectionError("Cache expired")`, so the outer account loop does a full
re-login anyway.

**Fix:** implement `relogin_account()` (delegate to
`process_account_uid_pass`/`process_account_token`) or remove the dead branch.

### 11. Blocking calls in the event loop (Medium)

`fetch_single_clan()` uses synchronous `requests.post(..., timeout=15)` inside an
`aiohttp` handler, iterating over every cached token. One slow token freezes the
whole dashboard (stats, logs, buttons) for up to 15 s each.

**Fix:** `await asyncio.to_thread(fetch_single_clan, ...)` or use `httpx.AsyncClient`
with `asyncio.gather`.

### 12. Dependency break (Medium)

`requirements.txt` does not pin `protobuf`; the generated modules import
`google.protobuf.runtime_version` (needs ≥ 5.27) and mix gencode 6.33.1
(`data_pb2`) with 7.35.1 (thunderFF family).

**Verified:** `protobuf==4.25.3` → `ImportError: cannot import name 'runtime_version'`;
`5.29.5` → `VersionError: mismatched Protobuf Gencode/Runtime major versions`
for `data_pb2`. `6.33.1` and `7.36.2` both work.

**Fix:** pin `protobuf>=6,<8` (7.36.2 tested) and pin every other dependency;
add `urllib3` explicitly.

### 13. Fake API-key auth (Medium)

`/api/guildinfo` checks `key == "SAHILOPS"` from the query string, and the same
key is hard-coded in `index.html`. It provides no real protection (key in URL,
logs, source) and is separate from the unprotected routes.

**Status: fixed.** The key is no longer hard-coded in the page or its URLs. The
server injects the configured key into a `<meta>` tag when serving `index.html`,
the UI sends it as `X-API-Key`, and `handle_api_guild_info` validates the header
with `hmac.compare_digest` (the `?key=` form is still accepted for scripts).
The default key remains `SAHILOPS`; override it with `SAHILOPS_API_KEY`.
The trade-off is unchanged in kind: a browser that can load the page can read
the key, so the key is an access gate for the local panel, not multi-user auth.

### 14. Version drift (Medium)

`ReleaseVersion` is `OB55` in the bot and `OB54` in the joiner; client versions
include `1.130.22` (baked packet), `1.132.6` (startup default), `1.132.9` (team
protobuf), `2.133.9` (generator). After a game update some paths will fail while
others still work, producing confusing half-broken behaviour.

**Fix:** single `version.py`/config with one source of truth, populated from the
already-implemented `version_config()`.

### 15. Stale account mode (Low)

`register_account()` computes `mode`/`mode_label` before the "existing account"
branch, but that branch updates level/EXP without recomputing them.

**Verified:** registering level 5 still reports `mode = "BR"`. It also conflicts
with the engine, which is Battle Royale only (`update_exp` even logs a switch to
"Lone Wolf").

**Fix:** recompute mode in the update branch (or drop the field).

### 16. Match-started counter (Low)

`total_matches_started` is only initialised and read; `increment_match_started()`
is never called.

**Fix:** call it when a match task is created (`core/app.py:2017`).

### 17. HTML injection into logs (Low)

`/api/logs` builds raw `<span>` HTML from log messages; the page assigns it with
`innerHTML`. Log text includes server-provided nicknames.

**Fix:** return JSON entries and render with `textContent`.

### 18. Duplicate handler (Low)

`handle_index` is defined twice; the first is dead.

**Fix:** delete one.

### 19. Hard-coded clan (Low)

`encode-id-clan.py` always encrypts `id_clan = 3034881538`.

**Fix:** accept a CLI argument.

---

## Potential risks (not proven)

| Risk | Why it matters | Where |
|---|---|---|
| Clan name/level may use the wrong fields (`special_code`, `rank`) | Dashboard can show a code instead of the name, and rank instead of level | `dashboard/dashboard_server.py:136–139` |
| `RUDP_DEAD` (107) is treated as match end | Bot leaves the UDP session on death; stats may count unfinished matches | `core/app.py:1342`, `core/message_ids.py` |
| Packet parsing is heuristic ("try offsets 10/8/6/12…, keep the longest dict") | Wrong parse can accept fake team invites or miss real ones | `core/app.py:1508`, `1444` |
| Team group ID / 7-digit team code are guessed from any matching string | Invite acceptance may target the wrong group | `core/app.py:1555–1589` |
| JWT split at character 660 | If token length changes, `sharma` is empty or truncated | `core/app.py:1087–1088` |
| Region rotation has no effect in squad mode | Leader/members use the account region, not `current_search_region` | `core/app.py:1771, 1798` |
| `informational` task may raise on a missing address and never restart | Chat channel silently disappears | `core/app.py:2356` |
| Multiple writers to `accounts.json` | Dashboard/generator/app can overwrite each other | `dashboard/dashboard_server.py:757`, `utils/SaMIGEN.py:700` |
| Infinite retry loops | Login every 15 s, generator waits forever if the API is down, joiner retries | `core/app.py:2395`, `utils/SaMIGEN.py:879` |
| Different fixed `X-GA-SV` values | One of the two is likely stale and triggers HTTP errors | `core/app.py:203` vs `utils/SaMIGEN.py:46` |
| Login payload reuse | `GetLoginData` reuses the `MajorLogin` body (access token in field 29) while the generator builds a separate body with the JWT | `core/app.py:2260` vs `utils/SaMIGEN.py:607` |
| No tests / no README / no config | Every change risks silent breakage; protocol values require source edits | whole repo |
| Terms of service | Automating live accounts can get them banned | — |

## Suggestions

See [upgrades.md](upgrades.md) for prioritised fixes. The three that matter most:
rotate the leaked tokens, authenticate/loopback the dashboard, and fix the
`CguFF_pb2`/pause/resume defects before trusting any status the UI shows.
