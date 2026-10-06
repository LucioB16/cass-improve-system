# Testing

## Deterministic tests (no live CASS)

```powershell
python -m unittest discover -s tests -v
```

38 tests, stdlib `unittest` only. Fixtures under `tests/fixtures/` cover
multiple agents/projects/dates. CASS subprocesses are mocked; the suite
never touches live session history.

Covered: period parsing (24h / N days / explicit ranges / rejects),
preflight exit codes (missing / old / uninitialized / healthy /
stale-refresh / stale-strict / broken), readiness classification
(healthy / stale-only / stale-plus-error-is-broken / uninitialized),
incremental-only refresh (never `--full`), adapter safety (`--robot`
always appended except `export`, non-cass refused, malformed JSON
actionable), malformed input, export-failure accounting, secret redaction
(tokens, passwords, private keys), signal extraction, cross-session
aggregation, cross-project vs project-scoped destinations, de-duplication,
occurrence accounting, honest empty reports, evidence traceability,
read-only pipeline (no files written).

## End-to-end validation (live CASS 0.10.0, 2026-10-06/07)

- Preflight: exit 0. Stale-only state observed live (`healthy:false`,
  `errors:["index stale"]`, age 2608s > 1800s threshold) → one bounded
  incremental `cass index` (+5 conversations in 23s) → healthy. The
  graded policy (healthy / stale→refresh→proceed / uninitialized→4 /
  broken→4, never auto `--full`) is covered by regression tests.
- 24h review via the installed `~/.agents` copy: 11 sessions discovered
  (opencode, 5 workspaces), full Inventory → Normalize → Aggregate →
  Report chain, exit 0. All 11 exports skipped: every session in the
  window shares the live opencode SQLite WAL that the reviewing session
  itself was writing — CASS panics on torn WAL reads (upstream bug,
  documented in `cass-integration.md`). Reported as coverage gaps.
- 7d review (40-session slice): 18 analyzed across codex / opencode /
  antigravity, 10 single-occurrence signals → do-not-promote, 0 spurious
  candidates. No strong clusters in this slice; mechanism verified by
  fixtures instead.
- Read-only proof: SHA-256 over repo + both global skill copies identical
  before/after the reviews. Reports returned in-conversation; nothing
  written to projects, skills, or instruction files.
- Skill discovery (OpenCode 1.18.18): `opencode debug skill` resolves
  `cass-improve-system` from `~/.agents/skills/` even with the
  `~/.claude/skills/` duplicate temporarily moved aside — the canonical
  copy works standalone. Note: duplicate skill names across locations are
  officially unsupported (first-found wins, order varies); both installed
  copies are currently byte-identical. Headless `opencode run` proof was
  blocked by a provider-side 404 in this environment, unrelated to skills.

Re-run the live review after any adapter change using the SKILL.md
workflow for `last 24h` and `last 7 days`; preflight now self-refreshes
stale-only state, so no manual `cass index` is needed first.
