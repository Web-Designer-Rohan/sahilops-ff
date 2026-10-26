# Structure

```text
SahilOps/
├── core/
│   ├── app.py                  Core engine: login, TCP gateway, squads, UDP match
│   ├── message_ids.py          UDP/RUDP message-id map
│   ├── thunderFF_pb2.py        Login and StartMatch protobuf schemas
│   ├── ReqCLan_pb2.py          Clan-join request schema
│   ├── encode_id_clan_pb2.py   Clan-id lookup schema
│   ├── data_pb2.py             Clan-info response schema
│   ├── output_pb2.py           Legacy output schema
│   ├── my_pb2.py               Legacy schema
│   └── StartMatch_pb2.py       Standalone StartUDPMatch schema
├── dashboard/
│   ├── dashboard_server.py     aiohttp server and bot_state
│   └── index.html              Single-page dashboard UI
├── utils/
│   ├── SaMIGEN.py              Bulk guest-account generator
│   ├── join_guild.py           Guild join/leave and owner request tool
│   ├── decode.py               Hex/packet inspector
│   ├── encode-id-clan.py       Clan-id encoder
│   ├── my_pb2.py               Joiner-local legacy schema
│   └── output_pb2.py           Joiner-local legacy schema
├── docs/                       Setup, architecture, audit, upgrade notes
├── archive/                    Original ZIP sources
├── accounts.json               Account input file
├── devices.json                Per-account device fingerprints
├── token_cache.json            Cached login sessions
├── guild_exp_cache.json        Guild XP baselines
├── requirements.txt
└── README.md
```

## Runtime data flow

```text
accounts.json ──► core.app login ──► token_cache.json / devices.json
                     │
                     ├─ TCP gateway (functional_addrs) ──► squad + matchmaking
                     │                                     └─ UDP match (server_ip:port)
                     └─ TCP chat (informational_addrs) ──► keep-alive / social packets
                     │
dashboard.dashboard_server.bot_state ◄── core.app status/EXP ──► dashboard/index.html
```

## Entry points

| Command | Purpose |
|---|---|
| `python -m core.app` | Main bot and dashboard |
| `python utils/SaMIGEN.py` | Generate guest accounts |
| `python utils/join_guild.py` | Guild join/leave and owner requests |
| `python utils/decode.py` | Inspect packet hex/JSON |
| `python utils/encode-id-clan.py` | Encode a clan ID |

## Shared runtime files

`core.app` and `dashboard.dashboard_server` resolve data files from the repository
root, so the bot can be launched from any working directory with
`python -m core.app`.
