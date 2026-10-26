# Workflow & Usage

## 0. Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins `protobuf>=6,<8`; the generated modules need it.

Put accounts in `accounts.json` at the repository root:

```json
[{"uid": "123456789", "password": "Passw0rd"}]
```

## A. Main bot — launch to match end

1. **Start** — from the repository root:

   ```bash
   python -m core.app
   ```

   Expect a `SahilOps` banner, then
   `Dashboard: http://localhost:20333`.

2. **Open** `http://localhost:20333`. You will see the guild XP card, session
   uptime bar, log terminal, `+ Add Account`, and `Stop Bot`.

3. **Add accounts** — click `+ Add Account`, enter the **UID** and password,
   then click **Save**.
   - Writes the account to `accounts.json` and starts a worker immediately.
   - Expect `[LOGIN] Guest <uid>...` then `[OK] <id> | <nick> | Lvl<n>`.
   - Token login is API-only: `POST /api/account/add {"token": "..."}`.

4. **Login chain (per account)** — guest register/token grant → `MajorLogin`
   (AES payload) → `GetLoginData` (JWT) → cache to `token_cache.json` →
   `[FUNC] TCP connected <uid> (DNS:… Lvl:… R:<region>)`.

5. **Squad formation** — the leader sets a target from the number of active
   account workers:
   - 1 account → solo matchmaking.
   - 2 accounts → duo.
   - 3 accounts → trio.
   - 4+ accounts → full four-player squad.
   - The leader waits up to `TEAM_PEER_WAIT_TIMEOUT` for expected peers, then
     falls back to the peers that are actually connected.
   - For squads, the leader sends `create_team`, invites each peer every 4 s for
     up to 120 s, and peers auto-accept incoming `05…` packets.
   - The leader logs `══ FINAL: In team (n/target) ══`, then loops
     `ready → 1 s → start` every 5 s. Members start the same loop after the
     handshake delay.

6. **Match found** — console prints `MATCH FOUND! Loading...` and
   `[PK] 🎯 Room Code / 🌐 Server / 🔑 UDP Key`.
   The bot switches to UDP: sends HELLO, answers ACK/PING, sends the startup
   frames, then starts `[AFK]` and `[MOVE]` workers.

7. **During the match** — `[MOVE] #n sent=…` every 30 packets; `[AFK]` logs when
   the match ends. The match counts as finished on command **103**
   (`RUDP_MATCH_END`), **107** (`RUDP_DEAD`), or after 90 s of silence.

8. **After the match** — `[MATCH #n] Closed`, EXP refresh ~9 s later, gateway
   reconnect after 6 s (`NEW_MATCH_DELAY`), and the cycle repeats.

### Dashboard buttons

| Button | Actual effect |
|---|---|
| `+ Add Account` | `POST /api/account/add` → saves account, starts worker |
| `Stop Bot` | `POST /api/account/pause_all` → sets pause flags and closes registered TCP writers; each loop waits while paused and reconnects on resume |
| `Restart Bot` | `POST /api/account/restart` with `{}` → still a no-op (see bugs #3) |
| `Clear` (logs) | clears the in-memory log buffer |
| page refresh | auto-POSTs `clanjoin` for a hard-coded clan (bugs #5) |

### HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | dashboard HTML |
| GET | `/api/stats` | totals, per-account objects, last 80 logs |
| GET | `/api/logs` | HTML-coloured log lines |
| POST | `/api/logs/clear` | clear logs |
| POST | `/api/account/add` | add guest `{uid,password}` or `{token}` |
| POST | `/api/account/delete` | delete account + caches + worker |
| POST | `/api/account/refresh` | refresh level/EXP |
| POST | `/api/account/pause` | pause one UID |
| POST | `/api/account/pause_all` | pause/resume all |
| POST | `/api/account/restart` | (broken) restart one/all |
| POST | `/api/clanjoin` | send clan join with every cached token |
| GET | `/api/guildinfo?guild_id=&region=&key=SAHILOPS` | guild XP JSON |

## B. Account generator — `utils/SaMIGEN.py`

1. `python utils/SaMIGEN.py`.
2. Enter **accounts to generate** (default 10) and **worker threads** (default 5,
   max 30), press **Enter**.
3. It registers a guest → gets token → `MajorRegister` (up to 10 nickname tries)
   → `ChooseNewbieChoice` → `MajorLogin` → `GetLoginData`.
4. Each success prints a card (`Nickname / UID / Password / JWT`) and appends
   `{uid, password, nickname, open_id, access_token, account_id, jwt, …}` to
   `accounts.json`; details go to `generator.log`.
5. Stops when the target count is reached (or Ctrl+C). If the API is
   unreachable it retries forever — see risks.

## C. Guild joiner / spammer — `utils/join_guild.py`

1. `python utils/join_guild.py`.
2. Choose `1` Guild Join, `2` Guild Leave, `3` OWNER SPAM (join + friend
   requests to the owner).
3. Enter the account file: `.txt` lines `uid:password` (also accepts `|`, `,`,
   space) or a JSON list of `{uid,password}`.
4. Enter the **Guild ID**. For option 3 the owner UID is auto-fetched, or you
   type it manually if that fails.
5. Set workers (1–10) and per-worker delay (default 0.8 s).
6. Watch per-account result cards and the final session report. Failed accounts
   are appended in **plain text** to `SahilOps/failed_accounts.txt`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ImportError: cannot import name 'runtime_version'` | old protobuf → `pip install "protobuf>=6,<8"` |
| `No accounts in accounts.json` | empty file; add via dashboard or generator |
| Login retries every 15 s | version scrape or login endpoint changed |
| Account connects but never queues | coordinator is waiting for configured peers; it falls back after `TEAM_PEER_WAIT_TIMEOUT` |
| `Stop Bot` pauses but does not resume | check the account pause state and web API response |
| Dashboard shows 0 matches started | counter never incremented (bugs #16) |
