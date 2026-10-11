# Installation

Prefer the automated route: paste the install prompt from the
[README](../README.md#install-with-your-coding-agent-recommended) into your
coding agent. This guide is for manual installation and troubleshooting.

All skill-installation commands below run from the **repository root**
(the scripts address each other relatively). Either clone the repository
or download it and work inside the extracted directory.

## Requirements

- A coding agent with Agent Skills support.
- Python 3 (`python` on Windows, `python3` on Linux/macOS) — the skill and
  its installer are stdlib-only.
- [CASS](https://github.com/Dicklesworthstone/coding_agent_session_search),
  installed separately (see below).
- Windows 10/11, a modern Linux distribution, or macOS.

## Supported platforms

| OS | Skill installer | Notes |
|---|---|---|
| Windows 10/11 | `scripts/install-skill.ps1` (PowerShell) | Junctions for compatibility paths |
| Linux | `scripts/install-skill.sh` (POSIX shell) | Symlinks; needs `python3` |
| macOS | `scripts/install-skill.sh` (POSIX shell) | Symlinks; needs `python3` |

## Install CASS

CASS is an external prerequisite and is not bundled.

### Windows

```powershell
irm https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.ps1 | iex
```

Installs SHA256-verified `cass.exe` into `~/.local/bin`; add that
directory to the user `PATH` so future shells and agents can find it.

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Dicklesworthstone/coding_agent_session_search/main/install.sh \
  | bash -s -- --easy-mode --verify
```

Notes (verified against the upstream installer source):

- Linux: prebuilt `linux-amd64`/`linux-arm64` binaries, glibc ≥ 2.28,
  installed to `~/.local/bin`.
- macOS Apple Silicon: prebuilt `darwin-arm64`.
- macOS Intel: no prebuilt binary is published; the installer falls back
  to building from source (requires git and the Rust toolchain).

Minimum supported CASS version: **0.8.0**; verified with **0.10.0**.

## Initialize or verify the CASS index

Check readiness first:

```text
cass --version
cass health --json
```

- If the index is healthy, do nothing further.
- If this is a first-time setup (`not_initialized`), one initial full
  build is required: `cass index --full`. This is the only full build the
  workflow needs; never rebuild an existing healthy index.
- If the index is merely stale, the skill's preflight performs one
  bounded incremental refresh automatically (never `--full`).

## Install cass-improve-system

### Manual Windows installation

From the repository root in PowerShell:

```powershell
# Canonical install + Claude and Cline compatibility links (default)
.\scripts\install-skill.ps1 -Target All

# Canonical install only
.\scripts\install-skill.ps1 -Target Agents

# One compatibility link only (canonical must already exist)
.\scripts\install-skill.ps1 -Target Claude
.\scripts\install-skill.ps1 -Target Cline
```

`-Target Auto` installs links only when `claude` is available, otherwise
the canonical copy alone. `-HomeDir` overrides the home directory (used
by tests); `-Source` overrides the skill source tree.

### Manual Linux/macOS installation

From the repository root:

```bash
# Canonical install + Claude and Cline compatibility symlinks (default)
sh scripts/install-skill.sh --target all

# Canonical install only
sh scripts/install-skill.sh --target agents

# One compatibility link only (canonical must already exist)
sh scripts/install-skill.sh --target cline
```

Supported `--target` values: `agents`, `both` (agents + Claude, kept for
backward compatibility), `claude`, `cline`, `all`. `--home` and `--source`
mirror the PowerShell options; `--force` permits overwriting.

## Canonical Skill layout

One physical tree is the source of truth:

```text
~/.agents/skills/cass-improve-system/   canonical skill
```

## Claude Code and Cline compatibility paths

```text
~/.claude/skills/cass-improve-system    compatibility path to canonical
~/.cline/skills/cass-improve-system     compatibility path to canonical
```

Directory junctions on Windows, symlinks on Linux/macOS. The operative
invariant is that each compatibility path **resolves to** the canonical
tree — not one mandatory filesystem shape: a pre-existing parent-level
alias (e.g. a whole-`.cline/skills` junction) is recognized as
`resolved-alias`, accepted, and never modified or removed by the tooling.
Reinstalls are idempotent; installing over an identical physical copy
migrates it to a link, while a divergent copy fails safely instead of
being overwritten.

## Verify installation

```powershell
# Windows
.\scripts\verify-install.ps1
```

```bash
# Linux/macOS
sh scripts/verify-install.sh
```

Verification compares a recursive SHA-256 manifest (bytecode excluded)
and classifies every compatibility path (correct, broken, wrong-target,
physical), failing with an explicit list on any mismatch.

## Reinstall/update

Re-run the installer with `-Force` / `--force`; existing correct links are
accepted, broken links repaired, and the canonical copy refreshed from the
current source. Verify afterward.

## Uninstall

Removing a compatibility link never touches the canonical directory;
removing the canonical directory requires explicit force:

```powershell
# Windows
.\scripts\uninstall-skill.ps1 -Target Claude
.\scripts\uninstall-skill.ps1 -Target Agents -Force
```

```bash
# Linux/macOS
sh scripts/uninstall-skill.sh --target cline
sh scripts/uninstall-skill.sh --target agents --force
```

Supported targets: `agents`, `both`, `claude`, `cline`, `all` (`all`
removes links first, canonical last). A physical (non-link) compatibility
directory is never removed implicitly.

## Troubleshooting

- `cass` not found: ensure `~/.local/bin` is on `PATH` in new shells.
- Preflight reports a missing CASS: install it per this guide, then re-run.
- Preflight reports an uninitialized index: run one `cass index --full`.
- Preflight reports a broken index: inspect `cass status --json` /
  `cass health --json`; resolve the reported cause, then re-run.
- Verification reports STALE/EXTRA files: reinstall with force, then verify.
- A compatibility path points somewhere unexpected: back it up or remove it
  manually, then re-run; the installer will not overwrite it blindly.

## Security considerations

- Review install scripts before piping them to a shell, as with any
  installer fetched from the network.
- The skill's default review is report-only; applying recommendations
  always requires explicit approval.
- Session content is treated as untrusted data; credentials are redacted
  before they can reach a report.
- CASS ingestion is local; if your coding agent uses remote LLM inference,
  that agent's own provider policy applies to whatever it sends there.
