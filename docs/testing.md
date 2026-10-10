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
incremental-only refresh (never `--full`), export recovery (nonzero-exit
with valid `-o` file recovers; malformed/error-envelope/empty rejected;
include-tools→plain fallback; bounded retries then honest gap),
manifest determinism + bytecode exclusion, adapter safety (`--robot`
always appended except `export`, non-cass refused, malformed JSON
actionable), malformed input, export-failure accounting, secret redaction
(tokens, passwords, private keys), signal extraction, cross-session
aggregation, cross-project vs project-scoped destinations, de-duplication,
occurrence accounting, honest empty reports, evidence traceability,
read-only pipeline (no files written), canonical install/link/uninstall
policy (correct/broken/wrong-target links, identical-vs-divergent physical
trees, idempotency, uninstall safety, spaces in paths), manifest
determinism.

CI (`.github/workflows/ci.yml`, matrix windows/ubuntu/macos) runs the unit
suite plus an isolated-HOME install → verify → reinstall → uninstall
exercise using the real wrappers; no private CASS history required.

## End-to-end validation (live CASS 0.10.0, 2026-10-10)

- 7d review via the installed `~/.agents` copy (stock skill, no custom
  normalizer): 13 discovered → **13 analyzed, 0 skipped**, 2 agents
  (opencode, antigravity), 6 workspaces. **11 sessions were recovered**
  from nonzero-exit CASS exports (exit 3221226505 WAL panic) via complete
  parseable `-o` files; the recovery count is reported under Coverage
  limitations. 3 medium-confidence candidates + 231 singles →
  do-not-promote. Full Inventory → Normalize → Aggregate → Report, exit 0.
- 24h review: 0 sessions in window (no coding activity) → honest empty
  report, exit 0.
- Install verification is now hash-based: `verify-install.ps1 -Source`
  compares a recursive SHA-256 manifest (bytecode excluded) and reports
  MISS/STALE/EXTRA per file; the installer wipes the destination first
  and fails closed on any mismatch. Both global copies verified
  byte-identical file-by-file after reinstall.

## Final fresh-session acceptance (PASSED, literal fresh OpenCode session)

- OpenCode resolved the global skill from:
  `C:\Users\lucio\.claude\skills\cass-improve-system`
- No development repository was inspected or used.
- Only stock installed skill scripts were executed.
- CASS 0.10.0 was healthy.
- Full workflow completed: Preflight → Inventory → Normalize → Aggregate → Report.
- 13 sessions discovered; 13 analyzed; **11 recovered** through the
  built-in degraded CASS export/WAL recovery path; 0 skipped.
- 2 agents represented: antigravity and opencode. 6 workspaces represented.
- 0 high-confidence recommendations. 3 medium/watch candidates, all
  correctly rejected after evidence verification. 231 single-occurrence
  signals correctly remained do-not-promote.
- No durable project, AGENTS.md, skill, repository, or learned-knowledge
  files were modified.
- The globally installed production skill **passed the final fresh-session
  acceptance test end-to-end**.

Note: OpenCode selected the `~/.claude` copy because duplicate skill names
across `~/.claude` and `~/.agents` are not guaranteed to resolve in a
deterministic order; standalone `~/.agents` discovery had already been
proven separately, and both installed copies were previously verified
byte-identical.

## Earlier validation history (superseded where noted)

- Preflight: exit 0. Stale-only state observed live (`healthy:false`,
  `errors:["index stale"]`, age 2608s > 1800s threshold) → one bounded
  incremental `cass index` (+5 conversations in 23s) → healthy. The
  graded policy (healthy / stale→refresh→proceed / uninitialized→4 /
  broken→4, never auto `--full`) is covered by regression tests.
- 0/11 incident (resolved): a 24h review once analyzed 0/11 because every
  session shared the live opencode WAL and CASS panicked after writing
  complete stdout exports. Fixed by the `-o` temp-file recovery path in
  `cass_adapter.py` (see `cass-integration.md`); the 2026-10-10 7d run
  above proves 13/13 with 11 recoveries through the stock skill.
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
