# Coding Skills

Skills are instruction packs for the coding agent, not runtime dependencies of the
bot. Installing them does not change `core/app.py` or add imports to the tool.

## Installed and relevant

### New in this audit (project scope)

| Skill | Source | Purpose | Why it fits |
|---|---|---|---|
| `async-python-patterns` | `wshobson/agents` (18K installs) | asyncio tasks, cancellation, timeouts, blocking-loop pitfalls | `core/app.py` is ~2,500 lines of asyncio with tasks, timeouts and swallowed cancels |
| `python-testing-patterns` | `wshobson/agents` (36.9K installs) | pytest fixtures, mocking, async tests | repo has no tests; pure encode/decode helpers are easy wins |
| `python-anti-patterns` | `wshobson/agents` | review checklist for common Python mistakes | audit found bare `except: pass`, duplicated config, dead code |

Installed with:

```bash
npx skills add wshobson/agents \
  -s async-python-patterns \
  -s python-testing-patterns \
  -s python-anti-patterns -y
```

Installer reported `Gen Safe`, `Socket 0 alerts`, `Snyk Low Risk` for all three,
and copied them to `.agents/skills/` (not symlinks). `skills-lock.json` now
contains 12 entries, including the three new ones.

**Verified:**

1. `npx skills ls` lists all three under *Project Skills* for OpenCode.
2. The three `SKILL.md` files and their `references/` folders exist locally.
3. The agent's skill catalog picked them up immediately (they appeared as
   available skills after install).
4. `async-python-patterns` was loaded through the skill tool and returned its
   full instructions, confirming the loader works.

### Already installed before this audit

| Skill | Purpose | Relevance here |
|---|---|---|
| `agent-browser` | browser automation CLI | not needed (no web UI testing requested) |
| `find-skills` | search/install skills | used to discover the Python skills |
| `reverse-engineer-anything` | guide for binary/artifact analysis | used as the audit method (unpacked archives, traced artifacts) |
| `rea-analysis-optimization`, `rea-ci-tests-audit`, `rea-changelog-update`, `rea-tool-design` | REA tooling for a different project (morluto/rea) | **irrelevant** to SahilOps; left installed, harmless |
| `improve-codebase-architecture` | architecture review workflow | references a `codebase-design` skill that is **not installed**, so it cannot run end-to-end |
| `grill-me` | "relentless interview" entry point | **broken stub:** its entire body is `Call the Skill tool with "grilling"`, and no `grilling` skill exists |

## Deliberately not installed

- `microsoft/azure-skills@python-appservice-deploy` — Azure deployment, irrelevant.
- `browser-use/browser-use` — duplicates `agent-browser`.
- `python-design-patterns` / `python-code-style` / `python-performance-optimization` —
  useful but not needed for a read-only audit; adding every Python skill would
  just bloat the prompt. Add `python-performance-optimization` later if the
  dashboard/event-loop work in `upgrades.md` is picked up.
- No skill was installed from an unknown author or with a failed security scan.

## Maintenance

```bash
npx skills ls                 # show installed skills
npx skills update             # update from sources
npx skills remove <skill>     # uninstall
```

`grill-me` and `improve-codebase-architecture` should be repaired by installing
their missing companion skills (`grilling`, `codebase-design`) or removed to
avoid dead prompts.

## Honest note

These skills provide workflow guidance only. They do not fix the bugs listed in
`bugs.md`, do not add tests automatically, and do not improve runtime speed by
themselves.
