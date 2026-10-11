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

- `export --include-tools` panics (exit 3221226505) on some DB-backed
  sessions. The adapter falls back to plain `--format json` automatically.
- Exporting sessions from a **live-mutating session DB** (e.g. opencode's
  SQLite WAL while that agent is actively working) can crash spuriously with
  `WAL frame salt mismatch` followed by a thread panic
  (exit 3221226505) — on plain exports too, not just `--include-tools`.
  Verified live: `cass export … -o <tmpfile>` still leaves a **complete,
  parseable JSON file** behind despite the trailing panic. The adapter
  therefore exports to a unique temp file, ignores stdout, and on nonzero
  exit validates the file (must parse to a message list; missing, empty,
  malformed, or error-envelope output is rejected and retried, then
  reported as an honest coverage gap). Salvaged output is flagged
  (`recovered_from_panic`) and counted in the report's Coverage
  limitations. Temp files are always cleaned up.
- `cass sessions` has `--since` but no `--until` on this version; the skill
  filters the end of the range client-side.
- Copilot Chronicle exports are raw source events, not an upstream defect:
  per-connector export shapes differ legitimately (Antigravity transcripts
  behave the same way), and CASS's own connector code documents nested
  `data.content` handling — so the adapter normalizes them downstream and
  no upstream issue was filed.
