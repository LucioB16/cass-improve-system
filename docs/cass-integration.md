# cass-integration

Verified against **cass 0.10.0** (installed 2026-10-06 via the official
Windows installer). Upstream:
`https://github.com/Dicklesworthstone/coding_agent_session_search`

## Installation per OS (from upstream installer sources)

Windows (PowerShell, verified 2026-10-06):

```powershell
irm https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.ps1 | iex
```

Installs SHA256-verified `cass.exe` to `~/.local/bin` (release asset
`cass-windows-amd64.zip`). Add that directory to the user `PATH`.

Linux / macOS (bash + curl, per upstream `install.sh`):

```bash
curl -fsSL https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.sh \
  | bash -s -- --easy-mode --verify
```

- Linux: prebuilt `linux-amd64`/`linux-arm64` tarballs, glibc ≥ 2.28;
  installs to `~/.local/bin` (easy-mode appends it to `~/.bashrc`/`~/.zshrc`).
- macOS Apple Silicon: prebuilt `darwin-arm64`.
- macOS Intel: **no prebuilt binary is published** — the installer falls
  back to building from source (requires git and the Rust toolchain).

Then build the index once: `cass index --full`. The skill resolves `cass`
through `PATH` on every platform; no platform-specific CASS behavior is
assumed beyond these upstream installation paths.

Validation after install:

```powershell
Get-Command cass
cass --version            # cass 0.10.0
cass health --json
cass index --full --json --no-progress-events
```

## Index state on this machine

- 497 conversations / ~137k messages across codex (263), opencode (168),
  antigravity (47), gemini (9), claude_code (8), omp (2).
- Data dir: `%APPDATA%\coding-agent-search\coding-agent-search\data`.

## CLI contract this skill relies on

- **Never run bare `cass`** — it launches an interactive TUI. All
  agent-facing calls use `--robot` (alias `--json`) for machine-readable output.
- Read operations pass `--no-maintenance`: strict read-only, no checkpoint
  refresh, no daemon spawn. `--refresh` (index catch-up) is never used by
  the review path. The single exception is preflight on stale-only state
  (see below): one bounded incremental `cass index`, never `--full`,
  mutating only CASS-derived index/archive state; uninitialized/broken
  states still stop and remain an explicit user action.
- `cass search "<q>" --robot --mode lexical --since <t> --fields summary
  --limit N --max-tokens N --no-maintenance` — corpus probing with a token budget.
- `cass sessions --since <t> --json --limit N [--workspace W]` —
  session enumeration. Accepts `--since` (`-24h`, `-7d`, ISO, `today`); no
  `--until` on this version (client-side filtering is used instead).
  IMPORTANT: `cass sessions` rejects `--agent` on 0.10.0
  (`error: unexpected argument '--agent' found`). The adapter therefore
  never passes `--agent` to `sessions`: agent-scoped inventory is built
  from the UNION of `sessions` and agent-filtered `timeline`
  (`--agent` IS supported there), because the two endpoints use different
  activity-time semantics and each misses sessions the other finds
  (verified live). Completeness is COMPLETE only when both sources are
  uncapped; otherwise UNVERIFIED, never a false COMPLETE.
- `cass timeline --since <t> [--until <u>] [--agent A] --json --group-by none` — flat
  session inventory with `started_at`/`ended_at` (ms epoch), agent,
  `source_path`, `message_count`, `source_id` (no workspace field; the
  adapter enriches it best-effort from a sessions fetch).
- `cass export <source_path> --source <id> --format json` — full message array
  (`role`, `content`, `timestamp`). Works for archive/DB-backed sessions.
  For Copilot Chronicle sources the export returns RAW events
  (`{type, data, timestamp}` with payloads nested under `data.content`),
  not normalized role/content messages — verified live against a
  synthetic `events.jsonl`. This is legitimate per-connector export output
  (same as Antigravity transcripts), so the adapter normalizes it
  downstream; no upstream change is implied.
- Copilot `tool.execution_complete` envelopes carry structured execution
  status: `success:bool` (present on 3327/3327 live completions),
  optional `shellExecution.exitCode`, `result:{content, detailedContent}`,
  `fileEdits`/`shellExecution` detail, and an `error:{code, message}`
  object on failures (39 observed); `tool.execution_start` carries
  `toolName` (top: powershell, view, apply_patch, edit, rg, create…).
  The normalizer consults this status for error-signal precision
  (see `references/analysis-rubric.md`) but never invents a status where
  the envelope has none.
- `cass view <path> --message-index <line_number> --json` — expand a single
  search hit from the archive without exporting the whole session.
- `cass health --json` / `cass status --json` — readiness; `initialized`,
  `stale`, and `maintenance-required` states drive preflight exit codes.
- `cass capabilities --json` — capability discovery (falls back to per-command
  `--help` probing on older versions).
- Time-filter syntax verified: `-24h`, `-7d`, `today`, `yesterday`, ISO
  `YYYY-MM-DD`, explicit `--since X --until Y` on timeline/search.

Minimum supported version: **0.8.0** (robot-mode contract). All CASS syntax
is isolated in `skill/cass-improve-system/scripts/cass_adapter.py`.

## Known upstream issues (affecting the skill design)

- `cass health --json` can report `unhealthy / ["index stale"]` (exit 1)
  purely on age (observed: age 2608s > 1800s threshold) while
  `cass status --json` simultaneously reports `index.status: ready`,
  `fresh: true`, `pending.sessions: 0`. Age-stale with zero pending is
  usable — the skill treats stale-only state (errors == `["index stale"]`,
  archive present, no other errors) as refreshable, never as broken.
- Stale-only refresh is one bounded incremental
  `cass index --json --no-progress-events` (never `--full`; verified:
  +5 conversations in ~23s, health green after). This writes only CASS's
  derived archive/index state and is classified as safe ingestion
  maintenance; the review itself stays read-only w.r.t. user data.

- `export --include-tools` can also fail nonzero (exit 3221226505) on some
  DB-backed sessions. The adapter falls back to plain `--format json`
  automatically.

- `cass sessions` has `--since` but no `--until` on this version; the skill
  filters the end of the range client-side.
- Copilot Chronicle exports are raw source events, not an upstream defect:
  per-connector export shapes differ legitimately (Antigravity transcripts
  behave the same way), and CASS's own connector code documents nested
  `data.content` handling — so the adapter normalizes them downstream and
  no upstream issue was filed.

### Upstream issue #519 — Windows export teardown abort

Upstream issue:
<https://github.com/Dicklesworthstone/coding_agent_session_search/issues/519>
(still open; its title names the reporter's initial
`WAL frame salt mismatch` symptom, which upstream later established is not
the cause).

**What was observed at the time (also reproduced by this project on opencode
sessions).** CASS 0.10.0 on Windows could successfully read an OpenCode
conversation, write a complete JSON export, announce export success — and
only then abort the process with `0xC0000409` (decimal 3221226505). The same
abort was observed on plain exports, not only with `--include-tools`.

**Root cause established upstream.** CASS accesses OpenCode SQLite through
`franken-agent-detection` (FAD). FAD maintained its own small async/Asupersync
runtime in thread-local storage, and that runtime survived until main-thread
TLS destruction during Windows `LdrShutdownProcess`. By then Windows had
already terminated the runtime's other threads, so the late teardown tried to
join thread/runtime state that could no longer be joined correctly: Rust
panicked in thread lifecycle cleanup with
`threads should not terminate unexpectedly`, and a panic at that
process-teardown stage aborts the process with `0xC0000409`.

The `WAL frame salt mismatch` warning is an **unrelated SQLite diagnostic**.
It may occur independently of the lifecycle failure and may still appear
during otherwise successful exports. It is not the cause of the teardown
panic, and neither is concurrent OpenCode writing.

**Upstream fix status (verified 2026-10-13).** Fixed on upstream `main`;
**not present in any official release** — the newest release is still
`v0.10.0` (2026-10-02).

- `4064d20824464edd007e562c559d8f96dbfb4e31` — pins
  `franken-agent-detection = 0.3.10`, calls FAD's `shutdown_driver()` (early
  shutdown of its SQLite bridge runtime) before CASS exits together with
  CASS's other two runtime shutdowns, and improves cleanup semantics so all
  shutdown calls are evaluated rather than letting an earlier failure or
  timeout skip another runtime. The same commit hardens direct OpenCode
  SQLite export selection: the requested database path and session ID must
  match, and a same-ID session in another OpenCode store is never silently
  substituted for the requested database.
- `b8726266d35cdea00156a58b968dcc30971e70c9` — compilation fix for the
  GH519 regression tests added by the commit above
  (`MessageRole::Assistant` → `MessageRole::Agent`). This was a test-only
  typo found during qualification, not part of the production bug.
- Native-Windows qualification reported upstream after both commits: GH519
  export regressions **8/8 PASS**, runtime shutdown tests **PASS**,
  live-writer exports either produce complete output with exit 0 or a clean
  read error with no published file, and no `0xC0000409` teardown abort.

**Downstream behavior in this skill — unchanged.** Because no released CASS
carries the fix yet, the defensive export recovery path stays enabled:
export to a unique temporary `-o` file, capture the process status, and on
nonzero exit still validate the file — accepting only structurally valid
conversation output and rejecting missing, empty, malformed, or
error-envelope output, with bounded retries before an honest coverage gap.
Verified live: a complete, parseable JSON file can still be left behind
despite the trailing panic. Salvaged output is flagged
(`recovered_from_panic`) and surfaced in the report's Coverage limitations
rather than hidden, and temp files are always cleaned up.

The recovery path may be reevaluated only after all of the following: an
official CASS release includes the GH519 production fix; this project
qualifies that released binary on its supported Windows workflow; complete
exports consistently exit 0; genuine read/output errors still fail cleanly;
and a compatibility policy for older supported CASS versions is decided. No
automatic removal is promised — a low-cost defensive path for older releases
is reasonable to keep.

### Separate upstream issue — zero-length WAL (FrankenSQLite, not #519)

Do not conflate this with GH519. When a writer executes
`PRAGMA wal_checkpoint(TRUNCATE)`, SQLite may temporarily leave the WAL at
zero bytes; FrankenSQLite could interpret that transient state as corrupt or
as recovery-in-progress, where SQLite treats it as an empty WAL with no
frames. Upstream tracks this separately as FrankenSQLite `bd-yxqs9`, with a
fix on FrankenSQLite `main`
(`8cc4796da8c6b7735cfd1a5f5d9940188f02a7da`) plus SQLite-writer regression
coverage. As of 2026-10-13 CASS `main` uses `frankensqlite = 0.4.9`, and no
published FrankenSQLite release containing that fix has been verified as
consumed by CASS — so this project claims nothing about it.

Historically this was never the failure this project hit: our #519 exports
produced **complete output files** before CASS aborted, whereas the
zero-length-WAL bug produces a read refusal and no published export. It is
noted here only to keep the two problems distinct.
