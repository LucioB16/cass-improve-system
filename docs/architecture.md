# Architecture

```
user request ("review the last 7 days")
        ↓
SKILL.md workflow (reasoning layer: the invoking agent)
        ↓
scripts/preflight.py ──→ cass --version / capabilities / health
   (one bounded incremental `cass index` refresh on stale-only state;
    never --full; CASS-derived state only)
        ↓ exit 0
scripts/collect_sessions.py ──→ cass sessions --since … (inventory with
   COMPLETE/CAPPED coverage, resolved UTC period bounds)
        ↓
scripts/normalize_cass.py ──→ cass export per session (-o recovery) →
   role normalization → provenance → full-scan signals with period
   provenance → stratified bounded retention → redaction →
   NormalizedSession JSON
        ↓ (batched per project on large corpora)
scripts/analyze.py ──→ session retry synthesis (per period class) →
   fingerprint clustering → provenance/period-aware confidence →
   related_ids
        ↓
scripts/report.py ──→ render-once findings + destination/category indexes →
   agent verifies against cited sessions, adds judgment → final report
   (in-conversation)
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
<home>/.claude/skills/cass-improve-system    compatibility path
<home>/.cline/skills/cass-improve-system     compatibility path
                                             (junction on Windows,
                                              symlink on Linux/macOS)
```

The invariant is resolution-to-canonical, not one filesystem shape: a
parent-level alias (e.g. a whole `.cline/skills` junction) is recognized
as `resolved-alias`, accepted, and never modified or removed by the tooling.

Reinstalls are idempotent; verification compares a recursive SHA-256
manifest (bytecode excluded) and additionally classifies each compatibility
path (correct / resolved-alias / broken / wrong-target / physical),
failing safely on drift.

## Evidence model

Provenance classes (structural role + envelope markers, never prose):
`user_text`, `assistant_text`, `tool_result`, `system`, `injected_system`
(directive wrappers presented as role=user), `unknown`. Eligibility,
confidence, and promotion all key off provenance.

Temporal classes per signal (source message timestamp vs resolved bounds):
`in_window`, `pre_window`, `post_window`, `unknown`, plus `unbounded`
legacy behavior when no bounds exist. Only in-window (+legacy unbounded)
evidence counts toward promotion; historical evidence corroborates and
displays without promoting; session-level retry synthesis groups
recurrence per period class with explicit period metadata.

## Key decisions

- CASS is an external executable dependency, never vendored.
- Read-only default with respect to projects, instructions, skills, and
  session data: `--no-maintenance` on every read; the single exception is
  preflight's bounded incremental refresh on stale-only state (never
  `--full`, CASS-derived state only).
- Context control: per-message/per-session truncation, `--max-tokens` on
  search, hierarchical batches instead of full-corpus concatenation.
- Persistent review output (only when requested) lives under
  `~/.agents/state/cass-improve-system/`, never inside the reviewed projects.
