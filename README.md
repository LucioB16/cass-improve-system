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

- One fresh session reviews **many historical sessions** across agents and projects.
- Flexible periods: last 24h, last N days, explicit date ranges (+ agent/workspace filters).
- Cross-session clustering with confidence scoring and conservative promotion policy.
- Detects: repeated mistakes, user corrections, debugging loops, duplicated
  research, manual procedures, automation/skill candidates, rule candidates,
  tooling gaps, and successful patterns worth preserving.
- Read-only by default; secrets redacted; no durable changes without approval.

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
~/.agents/skills/cass-improve-system/   = the real skill
~/.claude/skills/cass-improve-system    = compatibility link to the same skill
~/.cline/skills/cass-improve-system     = compatibility link to the same skill
                                          (directory junction on Windows,
                                           symlink on Linux/macOS)
```

A single physical copy means the two paths can never drift apart — an edit
to the canonical tree is instantly visible through the Claude path, and
reinstalls stay idempotent.

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

1. **Preflight** — verifies `cass` is present, compatible, and its index healthy.
2. **Inventory** — enumerates sessions in the period (agents, workspaces, coverage).
3. **Normalize** — exports sessions in batches, truncates for context budget,
   redacts secrets, extracts heuristic signals into a vendor-neutral schema.
4. **Aggregate** — clusters signals across sessions/projects, scores confidence.
5. **Report** — markdown decision document with traceable evidence; the agent
   verifies each candidate against its cited sessions before writing it up.

See `docs/architecture.md` and `skill/cass-improve-system/references/`.

## Safety

- Default run is **read-only**: `--no-maintenance` CASS reads, no index repair,
  no file edits. Applying any recommendation requires your explicit approval.
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
compatibility links at ~/.claude/skills/cass-improve-system/ and
~/.cline/skills/cass-improve-system/ (junctions on Windows, symlinks on
Linux/macOS) pointing at it.

Run the repository's verification steps after installation and report the
OS, installed CASS version, canonical skill path, link target, and
validation result.
```

## Compatibility

| Agent | Agent Skills support | Integration | E2E |
|---|---|---|---|
| OpenCode | verified current support | canonical `.agents` installation | passed (24h + 7d reviews) |
| Claude Code | verified | compatibility link to canonical | install path verified |
| Cline | native Agent Skills (no fork/adapter) | compatibility link at `.cline/skills` to canonical | passed on Cline 3.0.68 (13/13 analyzed, 11 WAL-recovered, 0 skipped) |

Cline reads the stock `SKILL.md` through its normal Skill mechanism;
`~/.agents` remains the single physical tree.

## License

MIT — see [LICENSE](LICENSE). CASS is a separate project with its own
license/terms; this project is not affiliated with or endorsed by CASS upstream.
