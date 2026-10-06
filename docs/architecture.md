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

## Key decisions

- CASS is an external executable dependency, never vendored.
- Read-only default: `--no-maintenance` on every read; index builds are an
  explicit separate step; the default run changes no durable files.
- Context control: per-message/per-session truncation, `--max-tokens` on
  search, hierarchical batches instead of full-corpus concatenation.
- Persistent review output (only when requested) lives under
  `~/.agents/state/cass-improve-system/`, never inside the reviewed projects.
