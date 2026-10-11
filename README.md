# cass-improve-system

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/LucioB16/cass-improve-system)](https://github.com/LucioB16/cass-improve-system/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/LucioB16/cass-improve-system)](https://github.com/LucioB16/cass-improve-system/commits/main)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/LucioB16/cass-improve-system/pulls)

**Turn a week of scattered coding-agent sessions into concrete improvements.**
A portable agent skill for **batch retrospective analysis** across all your
coding-agent history — repeated mistakes, user corrections, automation
candidates, skill ideas, and reusable patterns, all with traceable evidence.

## The problem

Coding-agent knowledge fragments across sessions and projects: the same
debugging loop in three repos, the same correction typed twice a week, a
manual procedure that should be a script. Per-session reflection hooks can't
see this — the pattern only exists *across* sessions.

## Batch retrospective, not a hook

```text
Open a fresh coding-agent session
        ↓
Ask for a review of a period (24h, 7 days, date range)
        ↓
CASS discovers sessions across agents/projects
        ↓
Hierarchical analysis: inventory → signals → cross-project clusters → scored findings
        ↓
Evidence-backed report (read-only by default)
```

You never run anything inside the original work sessions.

## Features

- One fresh session reviews **many historical sessions** across all
  CASS-visible agents and workspaces (OpenCode, Codex, Antigravity, …).
- Flexible periods: last 24h, last N days, explicit date ranges (+ agent/workspace filters).
- Full-session signal scanning with bounded stratified context (late-session
  evidence is found without concatenating whole transcripts).
- Multilingual correction detection (English + Spanish, accent-insensitive).
- Structured role normalization, including Antigravity transcripts.
- Provenance-aware analysis: user/assistant/tool/system/injected evidence
  is weighted differently; injected framework directives never count as
  user authorship.
- Period provenance per signal: in-window evidence promotes, historical
  evidence corroborates, inventory reports COMPLETE vs CAPPED.
- Session-level retry synthesis, provenance-aware confidence, stable
  finding IDs with related-finding links, render-once reports.
- Tolerates the known CASS post-write export failure by accepting complete
  validated exports (see CASS recovery below).
- Detects: repeated mistakes, user corrections, debugging loops, duplicated
  research, manual procedures, automation/skill candidates, rule candidates,
  tooling gaps, and successful patterns worth preserving.
- Read-only by default (see Safety); secrets redacted; no durable changes without approval.

## Dependency: CASS (required, external)

This skill uses **[CASS — Coding Agent Session Search](https://github.com/Dicklesworthstone/coding_agent_session_search)**
as its session-ingestion layer. CASS is a **separate upstream project** — it
must already be installed and is **not** bundled here.

Install CASS first:

```powershell
# Windows
irm https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.sh \
  | bash -s -- --easy-mode --verify
```

Then build the index once: `cass index --full`. Minimum supported version: 0.8.0
(robot-mode contract); verified with 0.10.0. See `docs/cass-integration.md`.

Platform notes (verified against upstream installer source):
- **Linux**: prebuilt `linux-amd64`/`linux-arm64` binaries, glibc ≥ 2.28;
  installs to `~/.local/bin` (add to `PATH`, e.g. via `~/.bashrc`).
- **macOS**: prebuilt `darwin-arm64` (Apple Silicon). **Intel Macs**: no
  prebuilt binary is published — the installer falls back to building from
  source (requires git and the Rust toolchain).
- Requires `curl` + `bash` for the one-liner; `python3` for this skill's scripts.

## Install the skill

One canonical installation, everywhere:

```text
~/.agents/skills/cass-improve-system/   = the real skill (one physical tree)
~/.claude/skills/cass-improve-system    = compatibility path to canonical
~/.cline/skills/cass-improve-system     = compatibility path to canonical
                                          (directory junction on Windows,
                                           symlink on Linux/macOS)
```

Supported installs use one canonical physical Skill tree, and every
compatibility path resolves to it — verification detects wrong-target or
divergent physical layouts and fails safely instead of proceeding on drift.
An update to the canonical copy is therefore instantly visible everywhere,
and reinstalls stay idempotent. (On machines where a whole skills directory
is already aliased, e.g. a parent-level `.cline/skills` junction, the
installer recognizes and preserves that layout instead of forcing one
filesystem shape.)

**Windows** (PowerShell):

```powershell
# Canonical install + Claude and Cline compatibility links (default)
.\scripts\install-skill.ps1 -Target All

# Canonical install only
.\scripts\install-skill.ps1 -Target Agents

# Cline compatibility link only (canonical must already exist)
.\scripts\install-skill.ps1 -Target Cline

# Verify (byte-identical manifest check)
.\scripts\verify-install.ps1
```

**Linux / macOS** (POSIX shell, requires `python3`):

```bash
# Canonical install + Claude and Cline compatibility symlinks (default)
sh scripts/install-skill.sh --target all

# Canonical install only
sh scripts/install-skill.sh --target agents

# Cline compatibility link only (canonical must already exist)
sh scripts/install-skill.sh --target cline

# Verify (byte-identical manifest check)
sh scripts/verify-install.sh
```

Uninstall (the Claude link is removed without touching the canonical copy;
removing the canonical copy requires `--force` / `-Force`):

```bash
# Linux/macOS
sh scripts/uninstall-skill.sh --target claude
sh scripts/uninstall-skill.sh --target agents --force
```

```powershell
# Windows
.\scripts\uninstall-skill.ps1 -Target Claude
.\scripts\uninstall-skill.ps1 -Target Agents -Force
```

Supported and CI-tested on Windows, Linux, and macOS. The skill's Python
runtime is stdlib-only and platform-neutral (UTF-8 enforced for all
piped JSON); CASS itself is resolved through `PATH` on every platform.

## Usage

```text
Use cass-improve-system to review the last 24 hours.
Analyze all my coding-agent sessions from the last 7 days and suggest improvements.
Review sessions from 2026-10-01 through 2026-10-06.
Find things I keep repeating that should become skills or automations.  # + --agent/--workspace filters available
```

## How it works

1. **Preflight** — verifies `cass` is present, compatible, and its index
   usable. On stale-only state it performs ONE bounded incremental
   `cass index` refresh (never `--full`); any other failure stops the
   review with an actionable message.
2. **Inventory** — enumerates sessions in the period (agents, workspaces,
   coverage) and reports explicitly whether coverage is COMPLETE or
   CAPPED at the inventory limit.
3. **Normalize** — exports sessions; scans the FULL export for signals;
   assigns provenance (user/assistant/tool/system/injected) and period
   provenance (in/pre/post-window); retains bounded stratified context
   for large sessions; redacts secrets.
4. **Aggregate** — synthesizes session-level retries; clusters findings
   across sessions/projects; applies provenance-aware and period-aware
   confidence (historical evidence corroborates but cannot manufacture a
   current-period promotion).
5. **Report** — stable-ID findings rendered once with traceable evidence
   and related-finding links; the agent verifies each candidate against
   its cited sessions before writing it up.

See `docs/architecture.md` and `skill/cass-improve-system/references/`.

## Safety

- Default run is **read-only with respect to projects, source repos,
  instructions, Skills, and original session data**: normal CASS reads use
  `--no-maintenance`, and applying any recommendation requires your
  explicit approval.
- One exception, confined to CASS-derived state: if preflight finds a
  merely stale index, it may run ONE bounded incremental `cass index`
  refresh (never `cass index --full`). This only updates CASS's own
  archive/index, never user data.
- Session content is treated as untrusted data; credential-looking values are
  redacted before they can reach a report.

## Output categories

High-confidence improvements · automation candidates · new-skill candidates ·
existing-skill improvements · global-rule candidates · project-specific
improvements · successful patterns · watch list · do-not-promote · coverage
limitations.

## Repository structure

```text
skill/cass-improve-system/  SKILL.md, scripts/, references/
scripts/                    skill_install.py (shared logic),
                            install/verify/uninstall-skill.ps1 (Windows),
                            install/verify/uninstall-skill.sh (Linux/macOS)
tests/                      deterministic unittest suite + fixtures
docs/                       architecture.md, cass-integration.md, testing.md
.github/workflows/          CI matrix: windows/ubuntu/macos
```

## Development / testing

```powershell
python -m unittest discover -s tests -v
```

Stdlib only — no third-party Python dependencies. Live CASS history is used
for integration validation only, never as a test dependency. CI runs the
suite plus an isolated-HOME install/verify/reinstall/uninstall exercise on
Windows, Ubuntu, and macOS.

## Agent install prompt

```text
Install the cass-improve-system skill from:
https://github.com/LucioB16/cass-improve-system

First detect the operating system. Then verify that CASS is installed and
working (cass --version). If it is missing, install it using the
upstream-supported method documented by the repository
(Windows: install.ps1 one-liner; Linux/macOS: install.sh one-liner with
bash; note Intel Macs build from source).

Install the skill canonically:
- Windows (PowerShell): .\scripts\install-skill.ps1 -Target All
- Linux/macOS (POSIX shell): sh scripts/install-skill.sh --target all

This creates the real skill at ~/.agents/skills/cass-improve-system/ plus
compatibility paths at ~/.claude/skills/cass-improve-system/ and
~/.cline/skills/cass-improve-system/ resolving to it (directory junctions
on Windows, symlinks on Linux/macOS; a pre-existing parent-level alias such
as a whole-`.cline/skills` junction is recognized and preserved).

Run the repository's verification steps after installation and report the
OS, installed CASS version, canonical skill path, link target, and
validation result.
```

## Compatibility

| Agent | Agent Skills support | Integration | E2E |
|---|---|---|---|
| OpenCode | verified current support | canonical `.agents` installation | passed (24h, 7d, 30-day reviews) |
| Claude Code | verified | compatibility path to canonical | install path verified |
| Cline | native Agent Skills (no fork/adapter) | compatibility path to canonical | passed on Cline 3.0.68 (13/13 analyzed, 11 WAL-recovered, 0 skipped) |
| Freebuff CLI | native global Skill discovery from canonical `.agents` | no project shadow/workaround | passed independent 30-day acceptance (207 sessions, 3 harnesses, 24 workspaces, 142 WAL recoveries, 0 skipped) |

Cline reads the stock `SKILL.md` through its normal Skill mechanism;
`~/.agents` remains the single physical tree.

Freebuff CLI support means the Freebuff command-line harness specifically;
Freebuff Desktop global-home-Skill behavior is NOT claimed.

## Validation

- 147/147 deterministic stdlib tests.
- CI 6/6: Windows / Ubuntu / macOS × unit tests + isolated-HOME
  install/verify/reinstall/uninstall exercise.
- Independent Freebuff CLI 30-day validation: 207 sessions, 3 harnesses,
  24 workspaces, 142 WAL recoveries, 0 skipped, inventory COMPLETE.

## CASS recovery

CASS 0.10.0 can write a complete `-o` JSON export and then exit nonzero
(post-write WAL panic on live session databases). The adapter accepts such
an export only after validating it parses to a genuine message payload;
missing, empty, malformed, or error-envelope output is still rejected, and
every salvage is reported as recovered coverage — a recovery path, not
suppression of arbitrary CASS failures. Upstream issue:
https://github.com/Dicklesworthstone/coding_agent_session_search/issues/519

## License

MIT — see [LICENSE](LICENSE). CASS is a separate project with its own
license/terms; this project is not affiliated with or endorsed by CASS upstream.
