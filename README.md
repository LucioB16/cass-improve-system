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

## Install the skill

```powershell
# Generic multi-agent path (default)
.\scripts\install-skill.ps1 -Target Agents

# Claude Code
.\scripts\install-skill.ps1 -Target Claude

# Verify
.\scripts\verify-install.ps1
```

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
scripts/                    install-skill.ps1, verify-install.ps1, uninstall-skill.ps1
tests/                      deterministic unittest suite + fixtures
docs/                       architecture.md, cass-integration.md, testing.md
```

## Development / testing

```powershell
python -m unittest discover -s tests -v
```

Stdlib only — no third-party Python dependencies. Live CASS history is used
for integration validation only, never as a test dependency.

## Agent install prompt

```text
Install the cass-improve-system skill from:
https://github.com/LucioB16/cass-improve-system

First verify that CASS is installed and working. If it is missing, install it using the upstream-supported method documented by the repository.

If you are Claude Code, install the skill globally under ~/.claude/skills/cass-improve-system/.
Otherwise, prefer the shared global path ~/.agents/skills/cass-improve-system/.

Run the repository's verification steps after installation and report the installed CASS version, skill path, and validation result.
```

## License

MIT — see [LICENSE](LICENSE). CASS is a separate project with its own
license/terms; this project is not affiliated with or endorsed by CASS upstream.
