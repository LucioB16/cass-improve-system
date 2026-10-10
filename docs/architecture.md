# Architecture

```
user request ("review the last 7 days")
        ↓
SKILL.md workflow (reasoning layer: the invoking agent)
        ↓
scripts/preflight.py ──→ cass --version / capabilities / health
        ↓ exit 0
scripts/collect_sessions.py ──→ cass sessions --since … (inventory + coverage)
        ↓
scripts/normalize_cass.py ──→ cass export per session (truncate + redact +
                             heuristic signals) → NormalizedSession JSON
        ↓ (batched per project on large corpora)
scripts/analyze.py ──→ cluster by (signal type, fingerprint), score confidence
        ↓
scripts/report.py ──→ markdown skeleton → agent verifies against cited
                       sessions, adds judgment → final report (in-conversation)
```

## Layering

- **CASS adapter** (`cass_adapter.py`): the only file that knows CASS CLI
  syntax. Everything else consumes plain JSON.
- **Mechanics** (`period`, `collect`, `normalize`, `analyze`, `report`):
  deterministic, stdlib-only, fully unit-tested with fixtures.
- **Judgment** (SKILL.md + references): the invoking agent interprets
  findings, verifies evidence, drafts proposals. Heuristic signals are leads,
  never verdicts.

## Installation model (canonical + link)

One physical skill tree, installed by the shared `scripts/skill_install.py`
core (thin `.ps1` / `.sh` launchers only forward arguments, so platforms
cannot diverge):

```text
<home>/.agents/skills/cass-improve-system/   canonical (source of truth)
<home>/.claude/skills/cass-improve-system    compatibility link
                                             (junction on Windows,
                                              symlink on Linux/macOS)
```

Reinstalls are idempotent; verification compares a recursive SHA-256
manifest (bytecode excluded) and additionally classifies the Claude path
(correct / broken / wrong-target / physical), failing safely on drift.

## Key decisions

- CASS is an external executable dependency, never vendored.
- Read-only default: `--no-maintenance` on every read; index builds are an
  explicit separate step; the default run changes no durable files.
- Context control: per-message/per-session truncation, `--max-tokens` on
  search, hierarchical batches instead of full-corpus concatenation.
- Persistent review output (only when requested) lives under
  `~/.agents/state/cass-improve-system/`, never inside the reviewed projects.
