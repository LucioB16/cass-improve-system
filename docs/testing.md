# Testing

## Deterministic tests (no live CASS)

```powershell
python -m unittest discover -s tests -v
```

27 tests, stdlib `unittest` only. Fixtures under `tests/fixtures/` cover
multiple agents/projects/dates. CASS subprocesses are mocked; the suite
never touches live session history.

Covered: period parsing (24h / N days / explicit ranges / rejects),
preflight exit codes (missing / old / uninitialized / healthy), adapter
safety (`--robot` always appended, non-cass refused, malformed JSON
actionable), malformed input, export-failure accounting, secret redaction
(tokens, passwords, private keys), signal extraction, cross-session
aggregation, cross-project vs project-scoped destinations, de-duplication,
occurrence accounting, honest empty reports, evidence traceability,
read-only pipeline (no files written).

## End-to-end validation (live CASS 0.10.0, 2026-10-06)

- Preflight: exit 0, index healthy (497 conversations).
- 24h review: inventory collected across opencode/codex sessions and
  multiple workspaces; normalize → analyze → report pipeline ran clean;
  no project files modified (report returned in-conversation).
- 7d review: 95 sessions in range; batched per-project normalization kept
  context bounded; candidates verified against cited sessions before
  being written up.
- Missing-CASS and incompatible-version paths covered by unit tests
  (exit 2 / 3 with actionable messages).

Re-run the live review after any adapter change:
`cass index` (incremental) first if the archive is stale, then the
SKILL.md workflow for `last 24h` and `last 7 days`.
