# Testing

## Deterministic tests (no live CASS)

```powershell
python -m unittest discover -s tests -v
```

147 tests, stdlib `unittest` only. Fixtures under `tests/fixtures/` cover
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
determinism, multilingual correction precision, provenance eligibility,
injected-system detection, stratified retention, period provenance and
period-aware promotion, COMPLETE/CAPPED inventory, session retry synthesis
(incl. mixed-period, numeric ordering, idempotency), template collapse,
related-ids dedup, UTF-16 input tolerance, Copilot Chronicle role/content
mapping (incl. legacy discriminators, explicit-role precedence, tool and
lifecycle handling, malformed envelopes), nested content extraction
(blocks, tool payloads, redaction, retention bounds), semantic coverage
states (full/partial/none/empty, control-only, malformed), timeline-based
agent filtering (union semantics, UNVERIFIED fallback, workspace server
flag), synthetic Copilot end-to-end (promotion, evidence refs, empty
corpus).

CI (`.github/workflows/ci.yml`, matrix windows/ubuntu/macos) runs the unit
suite plus an isolated-HOME install → verify → reinstall → uninstall
exercise using the real wrappers; no private CASS history required.

## Copilot Chronicle compatibility (synthetic + live-shape validation)

- `cass export --format json` on Copilot `events.jsonl` returns raw events
  (`{type, data, timestamp}`), verified live against a synthetic Chronicle
  file in TEMP (no index writes): 9/9 events round-tripped with keys
  `data/timestamp/type`. Same legitimate per-connector behavior as
  Antigravity transcripts — no upstream defect implied.
- Normalization maps dotted event types from an exact allow-list
  (`user.message` → user, `assistant.message` → assistant, tool execution
  events → tool, session/system/turn lifecycle → system), extracts nested
  `data.content` (plus `payload.content`, tool result/output/summary),
  and never dumps whole envelopes as messages.
- Tool outputs arrive nested (`data.result={content, detailedContent}`,
  verified live on Copilot CLI 1.0.94-3 exports): `_text_from_value`
  reads `text`, then `content`, then `detailedContent` from content-shaped
  dicts, so real tool output is preserved with `tool_result` provenance
  (enabling `error_mention` signals from tool output) instead of decoding
  to empty excerpts. Opaque dicts without those keys still yield "".
  The user's original `content` is always preferred over the much longer
  `transformedContent` (which embeds injected context).
- Semantic coverage per session (full/partial/none/empty) counts
  interpretable, unknown-role, and missing-content events; control-only
  sessions are `empty`, not failures; reports warn explicitly when
  sessions yield no interpretable content.
- Agent filtering no longer passes `--agent` to `cass sessions` (rejected
  on 0.10.0): agent-scoped inventory unions `sessions` with agent-filtered
  `timeline`, since the two endpoints use different activity-time
  semantics and each misses sessions the other finds (verified live:
  timeline found 9/11 opencode sessions). Completeness is COMPLETE only
  when uncapped on all sources, else UNVERIFIED — never a false COMPLETE.
- No local Copilot sessions exist on the validation machine
  (`~/.copilot/session-state` absent), so live-history Copilot E2E awaits
  independent retest; deterministic fixtures prove detection mechanics.

## Copilot App live validation (second machine, 2026-10-11)

Environment: Windows 11, Copilot CLI 1.0.94-3, CASS 0.10.0 healthy
(48 903 documents), 645 Copilot session directories on disk.

- 7d inventory: 39 sessions via `sessions`, 26 via `timeline`
  (sessions ⊇ timeline here; the reverse was observed for opencode on the
  first machine) — the UNION keeps all 39 with zero duplicates.
  Scoped `--agent copilot_cli` inventory reports COMPLETE; capped or
  client-filtered runs report UNVERIFIED, never false COMPLETE.
- `cass export` round-trips raw events 1:1 with `events.jsonl`
  (19/19, 179/179, 1597/1597, exit 0, no export recovery needed).
  No top-level `role`/`content`; payloads nested under `data.content`;
  timestamps monotonic and message-derived.
- Real `assistant.message` events carry `toolRequests` plus optional
  `reasoningText` (dropped uniformly, as for all vendors); empty-content
  tool-call turns count toward `missing_content` honestly.
- Real `tool.execution_complete` events carry
  `result={content, detailedContent}` — the flat-`output` fixture did not
  mirror this, so all tool excerpts decoded empty until the
  `_text_from_value` fallback fix (regression tests added).
- Stock 7d retrospective (`copilot_cli`, COMPLETE): 39/39 analyzed,
  0 skipped; user excerpts 516/528 non-empty, assistant 326/390;
  tool excerpts 0/254 before the fix → 523/659 after; `tool_result`
  signals 0 → 609; `error_mention` 266 → 875. Findings: 0 high,
  2 medium watch-list items (cross-project recurring tool errors with
  in-window evidence, correctly capped at medium as non-user evidence),
  remainder do-not-promote singles. Report states 39/39 partial with an
  explicit coverage limitation — a no-findings report never masquerades
  as a verified-empty corpus.

## Tool-error precision + event audit (same machine, 2026-10-11)

Both medium findings above were manually inspected and ruled FALSE
POSITIVES: (A) PowerShell source (`$ErrorActionPreference = "Stop"`)
returned by a successful `view` tool call matched bare `error`;
(B) research prose ("…still failed to refresh…") returned by a
successful `view` matched bare `failed`. Root cause: `error_mention`
fired on bare keywords in `tool_result` text with no regard for the
structured execution status (`success: true`, present on 3327/3327
live completions) or the read-vs-execute nature of the tool.

- Fix: `tool_status()` tristate (`ok` / `failed` / unknown) from
  `success`, `shellExecution.exitCode`, and `error` objects.
  Successful tool output needs a strong diagnostic shape (traceback
  header, `XxxError:`/`Exception:` line, nonzero-exit mention, counted
  test failures, build failure, permission/file-not-found, timeout,
  segfault); failed or status-less tool output keeps broad patterns.
  Structured `error.message` is surfaced as evidence; failures with no
  textual anchor stay conservatively silent (documented limitation).
  User/assistant paths and promotion thresholds unchanged.
- Same-corpus before/after (39 sessions, 34,442 events): `error_mention`
  875 → 419; `tool_result` signals 609 → 153 (93 structured-failed +
  60 success-with-strong-shape: 22 exception lines, 18 exit-code
  mentions, 10 counted failures, 8 file-not-found, 1 build, 1 denied;
  zero unexplained); assistant/user signals byte-identical (290/30);
  watch findings 2 → 0 (both FP titles absent from all groups);
  0 high; coverage 38 partial + 1 full, 0 none, 0 skipped.
- Event census (exact types, % of 34,442): hook.start/end 48.5%,
  tool start/complete 19.3%, assistant.message 8.8% (1935/3025 with
  text), assistant.turn_start/end 17.4%, user.message 1.7% (572/589),
  usage_checkpoint 1.7%, system.message 0.5%, binary_asset 0.4%,
  shutdown 0.3%, skill.invoked/resume/warning ≈ 0.25% each, plus 22
  rare lifecycle types (mode/canvas/compaction/truncation/subagent/
  external_tool/notifications, all ≤ 41 occurrences).
- Audited control/telemetry families now map to `system` (hooks,
  checkpoints, skill refs, subagent/canvas/compaction/mode lifecycle,
  binary assets, notifications) and external tool activity to `tool`
  (exact-type lists, never prefix wildcards). Unknown roles
  17,892 → 0 on this corpus; remaining `partial` coverage is empty
  tool-call turns (`missing_content`, still counted). Only genuinely
  unrecognized future types stay `?` — honest partials preserved.
- Deterministic suite: 191 → 226 (35 new: status tristate, 8 precision
  negatives incl. boilerplate/source/README/research/zero-errors,
  12 recall positives across all status paths, provenance guards,
  22-type control audit, aggregation proving boilerplate never
  promotes while genuine repeated failures stay eligible).

### Metric definitions (read carefully: three different denominators)

Independent summaries have mixed these up; all three are legitimate:

- Per-type content presence (audit census): user.message with text
  572/589, assistant.message with text 1935/3025, tool results with
  payload 3288/3327. Raw export-shape facts.
- Full-scan `interpretable_messages` (normalization metadata sum):
  5,834 on the 7d corpus — every raw event whose provenance is
  user/assistant/tool_result AND whose extracted text is non-empty.
  Counts events, not retained excerpts.
- Retained messages per role (bounded stratified context, ≤120/session):
  user 527 (515 non-empty), assistant 365 (301), tool 378 (240),
  system 1391. Full-scan signals always precede retention, so capping
  bodies never blinds detection.

### Native Copilot App retest (final, independent)

GitHub Copilot App invoked the installed Skill natively; all five stages
PASS (Preflight → Inventory → Normalize → Analyze → Report): 39/39
sessions, 34,442 raw events, zero unknown event types, coverage 38
partial + 1 full, `error_mention` 419, `tool_result` 153, 0 high,
0 medium, no false-positive recommendations. The two original
false-positive IDs from repository-level validation were not directly
present for re-matching (time-window shift), but the corrected behavior
is confirmed: same error classes now decode to no signal on successful
tool output, while structured-failure and strong-shape evidence still
promotes normally.

## End-to-end validation (live CASS 0.10.0, Phase 22 corrective build, 30-day corpus)

- Stock 30d review via the reinstalled canonical skill: 207 discovered
  (COMPLETE), 207 analyzed, 0 skipped, 142 export-recovered; opencode +
  codex + antigravity; 24 workspaces.
- Synthesized retry signals all carry explicit period; zero lack it.
- Pre-window-only recurrence contributes zero in-window evidence
  (the exact independent failure construction now yields no promotion;
  verified before/after: HIGH with phantom in_window=12 → nothing promoted).
- 0 high / 1 medium (a repeated request template with genuine in-window
  repetition — defensible under policy; one of its two cross-project
  occurrences is an assistant echo, so it is correctly capped at medium).
- export recovery, report-only behavior, and install/compat architecture
  unchanged.

### Earlier 30d run (pre-corrective, historical)

- Stock 30d review via the reinstalled canonical skill: 207 discovered
  (COMPLETE inventory, limit 1000), 207 analyzed, 0 skipped, 142
  export-recovered; opencode 142 + codex 52 + antigravity 13; 24 workspaces.
- The false high-confidence OH-MY-OPENCODE directive finding is gone
  (directive → `injected_system`, zero user evidence).
- 7 medium → 1 remaining medium (a repeated request template with genuine
  in-window repetition — defensible under policy, but note one of its two
  cross-project occurrences is an assistant echo of the user's request, so
  the evidence is thinner than the raw count suggests; correctly capped at
  medium, not a blocker); template/file-echo clusters demoted to low via
  template cap + corroboration rule.
- Normalize-emitted signals carry period provenance (3363 in-window,
  233 pre-window, 0 unknown in this run); pre-window evidence displays as
  corroboration, never promotes alone. Note: at the time of this run,
  analyze-stage synthesized retry signals did not yet carry period — that
  gap was the release-blocking bug fixed by the Phase 22 corrective
  (period-scoped synthesis) before v1.5.0.
- Spanish corrections live (23 signals incl. genuine cases); direct
  normalized manual (22) and retry (30) signals all user-authored
  (synthesized retries derive their period from their evidence class).
- export recovery, report-only behavior, and install/compat architecture
  unchanged.

- Stock 7d review via the reinstalled canonical skill: 13 discovered →
  13 analyzed, 0 skipped, 11 export-recovered (adapter unchanged and working);
  agents antigravity + opencode, 6 workspaces — same corpus as baseline.
- Spanish corrections now auto-detected (15 `user_correction` signals,
  including the previously-missed "NO pases todavía" class); Antigravity
  roles normalized (203 user / 429 assistant / 5 unknown, was ~all `?`).
- Long sessions use stratified retention (e.g. 1538→88 and 967→118
  messages, always ≤120); signals are full-scan so late corrections
  (e.g. `#msg354`) are found.
- All 13 sessions carry message-level activity ranges; 0 flagged
  out-of-period in this window (the stale-mtime case is covered by unit
  fixtures instead).
- Each finding id rendered exactly once; category sections and the
  destination index are title-only cross-references.
- Counts legitimately changed vs baseline (full-scan signals ~990 with
  signals_by_type/provenance in coverage; all findings low-confidence
  singles after tool-only false clusters were demoted from the watch list)
  because the fixes changed what is observable — see behavioral criteria
  in PLAN Phase 21.

### Earlier runs (historical)

- 7d review via the installed `~/.agents` copy (stock skill, no custom
  normalizer): 13 discovered → **13 analyzed, 0 skipped**, 2 agents
  (opencode, antigravity), 6 workspaces. **11 sessions were recovered**
  from nonzero-exit CASS exports (exit 3221226505, the Windows teardown
  abort described in `cass-integration.md`) via complete
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
  `C:\Users\<USERNAME>\.claude\skills\cass-improve-system`
- No development repository was inspected or used.
- Only stock installed skill scripts were executed.
- CASS 0.10.0 was healthy.
- Full workflow completed: Preflight → Inventory → Normalize → Aggregate → Report.
- 13 sessions discovered; 13 analyzed; **11 recovered** through the
  built-in degraded CASS export recovery path; 0 skipped.
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

## Cline fresh-session acceptance (PASSED, Cline 3.0.68 — already completed)

- Cline recognized `cass-improve-system` via its normal native Skill mechanism.
- Cline-visible path:
  `C:\Users\<USERNAME>\.cline\skills\cass-improve-system\`
  (resolves to the canonical `.agents` tree through a parent-level
  compatibility junction; no Skill modification needed).
- Full stock workflow ran: Preflight → Inventory → Normalize → Aggregate → Report.
- CASS 0.10.0; 13 sessions discovered, 13 analyzed, 11 export-recovered,
  0 skipped; 2 agents, 6 workspaces.
- 0 high-confidence recommendations; 3 watch-list candidates, all correctly
  rejected after evidence inspection; 231 singles remained do-not-promote.
- No durable project/Skill/repository/learned-knowledge modifications.
- Result: PASS — Cline discovered and executed the globally installed
  skill end-to-end with no Cline-specific changes.

Local Windows usernames are anonymized in this published documentation; the
validation runs themselves happened on real Windows machines and are
recorded as they occurred.

Distinguish: the above is the real Windows Cline E2E; filesystem/install
behavior on Windows/Linux/macOS is covered separately by CI with isolated
HOME directories (no Cline or private history required).

## Windows UTF-8 shell note

PowerShell `>` redirection can produce UTF-16 files while the pipeline
expects UTF-8 JSON. The workflow itself never depends on redirection
(pipes + UTF-8 byte I/O throughout); as a minimal safeguard the pipeline
entry points additionally accept UTF-16 BOM/sniffed input. No Skill change
was needed for the Cline acceptance test.

## Earlier validation history (superseded where noted)

- Preflight: exit 0. Stale-only state observed live (`healthy:false`,
  `errors:["index stale"]`, age 2608s > 1800s threshold) → one bounded
  incremental `cass index` (+5 conversations in 23s) → healthy. The
  graded policy (healthy / stale→refresh→proceed / uninitialized→4 /
  broken→4, never auto `--full`) is covered by regression tests.
- 0/11 incident (resolved): a 24h review once analyzed 0/11 because CASS
   panicked after writing complete exports on every session — the Windows
   teardown abort later diagnosed upstream as issue #519 (see
  `cass-integration.md`; the accompanying `WAL frame salt mismatch` warning
  is an unrelated SQLite diagnostic). Fixed by the `-o` temp-file recovery
  path in `cass_adapter.py`; the 2026-10-10 7d run
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
