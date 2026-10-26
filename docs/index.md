# SahilOps — Documentation

Documentation for the SahilOps Free Fire automation workspace.

| Doc | Read this to learn |
|---|---|
| [overview.md](overview.md) | What the tool does and its key features |
| [tech-stack.md](tech-stack.md) | Languages, libraries, protocols, external APIs |
| [workflow.md](workflow.md) | Step-by-step setup and use |
| [structure.md](structure.md) | Every file/folder and its job |
| [bugs.md](bugs.md) | Confirmed bugs vs. risks, severity, fixes |
| [upgrades.md](upgrades.md) | High-impact improvements and priorities |
| [skills.md](skills.md) | Installed coding skills and how they were verified |

**Short version:** SahilOps logs into Free Fire accounts, builds a squad sized
to the active accounts, queues Battle Royale, and keeps the UDP match session
alive while a local web dashboard shows guild XP and logs. Two utility scripts
generate accounts and handle guild join / friend-request workflows.

> ⚠️ It automates live game accounts, breaks the game's terms of service, and
> uses session tokens inside `token_cache.json`. Treat the workspace as
> compromised until those credentials are rotated.
