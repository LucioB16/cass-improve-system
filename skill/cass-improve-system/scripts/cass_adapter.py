"""Narrow adapter over the external CASS CLI.

All CASS-specific syntax lives here. The rest of the skill imports this
module so upstream CLI changes require edits in exactly one place.

Safety rules enforced here:
- Every invocation uses machine-readable flags (--robot/--json). Bare `cass`
  would launch an interactive TUI and must never be invoked.
- Read operations pass --no-maintenance so a review never mutates the index.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

CASS_UPSTREAM = "https://github.com/Dicklesworthstone/coding_agent_session_search"
MIN_CASS_VERSION = (0, 8, 0)

# Capabilities the skill relies on (verified against cass 0.10.0).
REQUIRED_COMMANDS = ("search", "sessions", "timeline", "view", "export", "health", "status")


class CassError(Exception):
    """Raised when a cass invocation fails. `actionable` is shown to the user."""

    def __init__(self, message: str, actionable: str = ""):
        super().__init__(message)
        self.actionable = actionable


def find_cass() -> str | None:
    """Locate the cass executable on PATH. Returns None when missing."""
    return shutil.which("cass")


def _run_raw(argv: list[str], timeout_s: int = 120) -> subprocess.CompletedProcess:
    if not argv or argv[0] != "cass":
        raise CassError("Refusing to run a non-cass command through the adapter.")
    exe = find_cass()
    if exe is None:
        raise CassError(
            "CASS executable not found on PATH.",
            f"CASS is required but not installed. Install it from {CASS_UPSTREAM} "
            "(Windows: see docs/cass-integration.md), then re-run.",
        )
    try:
        return subprocess.run(
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise CassError(
            f"cass {' '.join(argv[1:])} timed out after {timeout_s}s.",
            "Re-run with a narrower period or smaller --limit; do not retry blindly.",
        ) from exc


def run_cass_json(
    argv: list[str],
    timeout_s: int = 120,
    machine_flag: str | None = "--robot",
) -> dict | list:
    """Run cass with machine-readable output and parse stdout as JSON.

    Callers pass argv WITHOUT the output flag; machine_flag is appended so
    no caller can accidentally trigger the interactive TUI. Commands whose
    machine-readable mode is a different flag (e.g. `export --format json`,
    which rejects --robot) pass machine_flag=None and include their own flag.
    """
    if machine_flag and machine_flag not in argv and "--json" not in argv:
        argv = [*argv, machine_flag]
    proc = _run_raw(["cass", *argv], timeout_s=timeout_s)
    if proc.returncode != 0:
        hint = _extract_error_hint(proc.stdout) or proc.stderr.strip()[-500:]
        raise CassError(f"cass {' '.join(argv)} failed (exit {proc.returncode}).", hint)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CassError(
            "cass returned non-JSON output; the installed version may be incompatible.",
            f"Run `cass --version` and compare against docs/cass-integration.md. ({exc})",
        ) from exc


def _extract_error_hint(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(payload, dict):
        err = payload.get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", "")
            hint = err.get("hint", "")
            return f"{msg} {hint}".strip()
    return ""


def parse_version(text: str) -> tuple[int, ...]:
    """Parse 'cass 0.10.0' (or similar) into a comparable tuple."""
    for token in text.replace(",", " ").split():
        parts = token.strip().split(".")
        if len(parts) >= 2 and all(p.isdigit() for p in parts):
            return tuple(int(p) for p in parts)
    return (0,)


def get_version() -> tuple[int, ...]:
    proc = _run_raw(["cass", "--version"], timeout_s=30)
    if proc.returncode != 0:
        raise CassError("`cass --version` failed.", proc.stderr.strip()[-500:])
    return parse_version(proc.stdout.strip())


def check_capabilities() -> tuple[bool, str]:
    """Verify the installed cass exposes the commands this skill needs."""
    try:
        data = run_cass_json(["capabilities"], timeout_s=30)
    except CassError:
        # Older versions lack `capabilities`; fall back to per-command --help.
        missing = []
        for cmd in REQUIRED_COMMANDS:
            proc = _run_raw(["cass", cmd, "--help"], timeout_s=30)
            if proc.returncode != 0:
                missing.append(cmd)
        if missing:
            return False, f"missing commands: {', '.join(missing)}"
        return True, "verified via --help fallback"
    if isinstance(data, dict):
        return True, f"api v{data.get('api_version', '?')}, contract v{data.get('contract_version', '?')}"
    return True, "capabilities endpoint responded"


# --- Read-only query wrappers (always --no-maintenance) ---------------------

READ_FLAGS = ["--no-maintenance"]


def search(
    query: str,
    since: str | None = None,
    limit: int = 20,
    mode: str = "lexical",
    agent: list[str] | None = None,
    workspace: list[str] | None = None,
    max_tokens: int = 6000,
    timeout_s: int = 120,
) -> dict:
    argv = ["search", query, "--mode", mode, "--limit", str(limit),
            "--max-tokens", str(max_tokens), "--fields", "summary", *READ_FLAGS]
    if since:
        argv += ["--since", since]
    for a in agent or []:
        argv += ["--agent", a]
    for w in workspace or []:
        argv += ["--workspace", w]
    result = run_cass_json(argv, timeout_s=timeout_s)
    assert isinstance(result, dict)
    return result


def list_sessions(
    since: str | None = None,
    limit: int = 200,
    agent: list[str] | None = None,
    workspace: list[str] | None = None,
    timeout_s: int = 120,
) -> list[dict]:
    argv = ["sessions", "--limit", str(limit)]
    if since:
        argv += ["--since", since]
    for a in agent or []:
        argv += ["--agent", a]
    for w in workspace or []:
        argv += ["--workspace", w]
    result = run_cass_json(argv, timeout_s=timeout_s)
    if isinstance(result, dict):
        sessions = result.get("sessions", [])
    elif isinstance(result, list):
        sessions = result
    else:
        sessions = []
    return sessions if isinstance(sessions, list) else []


def timeline(
    since: str,
    until: str | None = None,
    agent: list[str] | None = None,
    timeout_s: int = 120,
) -> dict:
    argv = ["timeline", "--since", since, "--group-by", "none"]
    if until:
        argv += ["--until", until]
    for a in agent or []:
        argv += ["--agent", a]
    result = run_cass_json(argv, timeout_s=timeout_s)
    assert isinstance(result, dict)
    return result


def export_session(
    source_path: str,
    source_id: str = "local",
    timeout_s: int = 120,
    retries: int = 3,
) -> list[dict]:
    # `export` rejects --robot/--json; --format json IS its machine-readable
    # mode. --include-tools surfaces tool calls, but cass <=0.10.0 can panic
    # on some DB-backed sessions with that flag (upstream bug), so fall back
    # to a plain export instead of failing the session. Reads against a
    # live-mutating session DB can also crash spuriously (WAL race), so retry
    # a few times before giving up and reporting a coverage gap.
    last_error: CassError | None = None
    for _ in range(max(1, retries)):
        for argv in (
            ["export", source_path, "--source", source_id, "--format", "json", "--include-tools"],
            ["export", source_path, "--source", source_id, "--format", "json"],
        ):
            try:
                result = run_cass_json(argv, timeout_s=timeout_s, machine_flag=None)
                break
            except CassError as exc:
                last_error = exc
        else:
            time.sleep(2)
            continue
        break
    else:
        raise last_error or CassError(f"export failed for {source_path}")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("messages", "conversation", "items"):
            if isinstance(result.get(key), list):
                return result[key]
    return []


def health() -> dict:
    result = run_cass_json(["health"], timeout_s=60)
    return result if isinstance(result, dict) else {}
