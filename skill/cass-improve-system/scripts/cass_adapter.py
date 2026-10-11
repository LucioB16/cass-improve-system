#!/usr/bin/env python3
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
import os
import shutil
import subprocess
import tempfile
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
    until: str | None = None,
) -> list[dict]:
    """Backward-compatible inventory fetch. Prefer fetch_inventory(), which
    also reports how complete the result is. Extra agent filters never reach
    `cass sessions` (it rejects --agent on 0.10.0); they use the timeline
    path or client-side filtering inside fetch_inventory()."""
    sessions, _info = fetch_inventory(
        since=since, limit=limit, agent=agent, workspace=workspace,
        timeout_s=timeout_s, until=until)
    return sessions


def _ms_epoch_to_iso(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(value) / 1000.0,
                                         tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _timeline_filtered(since: str | None, until: str | None,
                       agents: list[str],
                       timeout_s: int) -> tuple[list[dict], bool]:
    """Server-side agent filtering via `cass timeline` (which accepts
    --agent, unlike `cass sessions` on 0.10.0). Returns (entries, complete).

    Timeline entries carry no workspace and use different activity-time
    semantics than `sessions` (verified live: each misses sessions the
    other finds), so callers must UNION both sources, never substitute.
    Entries are mapped to inventory shape with ISO timestamps.
    """
    argv = ["timeline", "--group-by", "none"]
    if since:
        argv += ["--since", since]
    if until:
        argv += ["--until", until]
    for a in agents:
        argv += ["--agent", a]
    result = run_cass_json(argv, timeout_s=timeout_s)
    if not isinstance(result, dict):
        raise CassError("timeline returned unexpected output; "
                        "cannot verify agent-filtered inventory.")
    items = result.get("sessions", [])
    if not isinstance(items, list):
        raise CassError("timeline returned unexpected output; "
                        "cannot verify agent-filtered inventory.")
    total = result.get("total_sessions")
    complete = True if total is None else len(items) >= int(total)
    sessions = []
    for entry in items:
        if not isinstance(entry, dict) or not entry.get("source_path"):
            continue
        started = _ms_epoch_to_iso(entry.get("started_at"))
        ended = _ms_epoch_to_iso(entry.get("ended_at")) or started
        sessions.append({
            "path": entry.get("source_path"),
            "agent": entry.get("agent", "?"),
            "workspace": "?",
            "source_id": entry.get("source_id", "local"),
            "title": entry.get("title"),
            "started_at": started,
            "ended_at": ended,
            "modified": ended or started,
            "message_count": entry.get("message_count"),
        })
    return sessions, complete


def fetch_inventory(
    since: str | None = None,
    limit: int = 200,
    agent: list[str] | None = None,
    workspace: list[str] | None = None,
    timeout_s: int = 120,
    until: str | None = None,
) -> tuple[list[dict], dict]:
    """Enumerate sessions plus machine-readable completeness info.

    Routing (all inside this adapter):
    - No agent filter: `sessions` (--workspace is server-supported there);
      limit+1 probe gives exact COMPLETE/CAPPED.
    - Agent filter: UNION of `sessions` and agent-filtered `timeline`,
      because the two endpoints use different activity-time semantics and
      each misses sessions the other finds (verified live). Timeline-only
      substitution would silently drop sessions.
    Client-side agent/workspace filtering applies over the merged set.
    COMPLETE requires an uncapped sessions fetch AND (when applicable) a
    complete timeline result AND complete workspace enrichment; otherwise
    UNVERIFIED (never a false COMPLETE). Trims to `limit`.
    Returns (sessions, {"via", "complete": bool|None, "detail"}).
    """
    agents = [a for a in (agent or []) if a]
    workspaces = [w for w in (workspace or []) if w]
    argv = ["sessions", "--limit", str(limit + 1)]
    if since:
        argv += ["--since", since]
    for w in workspaces:
        argv += ["--workspace", w]
    result = run_cass_json(argv, timeout_s=timeout_s)
    if isinstance(result, dict):
        base = result.get("sessions", [])
    elif isinstance(result, list):
        base = result
    else:
        base = []
    if not isinstance(base, list):
        base = []
    raw_capped = len(base) > limit
    ws_map = {s.get("path"): s.get("workspace") for s in base
              if isinstance(s, dict) and s.get("path")}
    enrich_complete = not raw_capped
    timeline_complete = True
    if agents:
        try:
            extra, timeline_complete = _timeline_filtered(
                since, until, agents, timeout_s)
        except CassError:
            # Older CASS without timeline agent support: sessions only.
            timeline_complete = False
        else:
            seen = {s.get("path") for s in base
                    if isinstance(s, dict)}
            for entry in extra:
                if entry["path"] not in seen:
                    entry["workspace"] = ws_map.get(entry["path"]) or "?"
                    base.append(entry)
                    seen.add(entry["path"])
    if agents:
        base = [s for s in base
                if isinstance(s, dict) and s.get("agent") in agents]
    if workspaces:
        base = [s for s in base
                if isinstance(s, dict) and s.get("workspace") in workspaces]
    trimmed = len(base) > limit
    sessions = base[:limit]
    if not agents:
        if raw_capped or trimmed:
            info = {"via": "sessions", "complete": False,
                    "detail": "limit+1 probe"}
        else:
            info = {"via": "sessions", "complete": True,
                    "detail": "full period set fetched"}
    elif raw_capped or trimmed or not timeline_complete or \
            (workspaces and not enrich_complete):
        info = {"via": "sessions+timeline", "complete": None,
                "detail": "agent filter over merged sources; matching "
                          "sessions may exist beyond the fetched window"}
    else:
        info = {"via": "sessions+timeline", "complete": True,
                "detail": "uncapped sessions fetch plus complete "
                          "server-side agent-filtered timeline"}
    return sessions, info


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


def _read_export_file(path: str) -> list[dict] | None:
    """Validate a `-o` export file. Returns messages, or None when the file
    is missing, empty, malformed, an error envelope, or the wrong shape.

    A torn write cannot yield fully parseable JSON, so a file that parses
    to a message list is accepted even when CASS exited nonzero afterwards.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        if "error" in data:
            return None
        for key in ("messages", "conversation", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return None
    if not isinstance(data, list):
        return None
    return data


def export_session(
    source_path: str,
    source_id: str = "local",
    timeout_s: int = 120,
    retries: int = 3,
) -> tuple[list[dict], bool]:
    """Export one session, tolerating the upstream WAL/panic race.

    Returns (messages, recovered). `recovered=True` means the payload was
    salvaged from a `-o` file left behind by a nonzero-exit export; the
    caller must surface that as a coverage note.

    Strategy (all inside this adapter):
    - `export` rejects --robot/--json; `--format json -o <tmpfile>` IS its
      machine-readable mode. Nothing is parsed from stdout.
    - Unique temp file per attempt (system temp dir; read-only w.r.t. all
      user/session data), always cleaned up.
    - Try `--include-tools` first, fall back to plain export.
    - On nonzero exit, still validate the `-o` file: CASS 0.10.0 can write
      a complete export and panic afterwards (WAL frame salt mismatch).
    - Missing/malformed/empty/error-envelope output is rejected, never
      mistaken for success; after bounded retries the session raises
      CassError so it becomes an honest coverage gap.
    """
    variants = (
        ["--include-tools"],
        [],
    )
    last_error: CassError | None = None
    for _ in range(max(1, retries)):
        for extra in variants:
            fd, tmp = tempfile.mkstemp(prefix="cass-export-", suffix=".json")
            os.close(fd)
            try:
                try:
                    proc = _run_raw(
                        ["cass", "export", source_path, "--source", source_id,
                         "--format", "json", *extra, "-o", tmp],
                        timeout_s,
                    )
                except CassError as exc:
                    last_error = exc
                    continue
                messages = _read_export_file(tmp)
                if messages is not None:
                    return messages, proc.returncode != 0
                if proc.returncode == 0:
                    last_error = CassError(
                        f"cass export wrote unusable output for {source_path}.")
                else:
                    last_error = CassError(
                        f"cass export {source_path} failed (exit "
                        f"{proc.returncode}) with no recoverable output.")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        time.sleep(2)
    raise last_error or CassError(f"export failed for {source_path}")


def health() -> dict:
    result = run_cass_json(["health"], timeout_s=60)
    return result if isinstance(result, dict) else {}


def readiness() -> dict:
    """Combined health + status snapshot used to grade index readiness."""
    snapshot = {"health": health(), "status": {}}
    try:
        status = run_cass_json(["status"], timeout_s=60)
        if isinstance(status, dict):
            snapshot["status"] = status
    except CassError:
        pass  # status is advisory; classification falls back to health alone
    return snapshot


def classify_readiness(snapshot: dict) -> tuple[str, str]:
    """Grade CASS readiness. Returns (state, detail) with state in:
    healthy | stale | uninitialized | broken.

    Only stale EXCLUSIVELY by age (errors == ["index stale"], archive
    present and openable, no other errors) counts as stale. Anything else
    unhealthy is broken: a stale label must never mask real damage.
    """
    h = snapshot.get("health", {}) or {}
    s = snapshot.get("status", {}) or {}
    if not h.get("initialized", False) or h.get("status") in ("not_initialized",) \
            or s.get("status") in ("not_initialized",):
        return ("uninitialized",
                f"index status '{h.get('status', s.get('status', '?'))}': archive not built")
    if h.get("healthy", False):
        return ("healthy", "index healthy")
    errors = [str(e) for e in h.get("errors", []) or []]
    db = h.get("db", {}) or {}
    pending = (s.get("pending", {}) or {}).get("sessions", "?")
    if errors and all("stale" in e.lower() for e in errors) and not db.get("open_error"):
        return ("stale",
                f"index stale by age only (pending sessions: {pending}); "
                "archive present and readable")
    detail = "; ".join(errors) or h.get("root_cause") or s.get("root_cause") \
        or h.get("status", "unknown failure")
    if db.get("open_error"):
        detail += f" | db open_error: {db['open_error']}"
    return ("broken", str(detail))


def refresh_index_incremental(timeout_s: int = 300) -> dict:
    """Bounded incremental catch-up for stale-only state.

    Upstream-prescribed (`cass index`, NOT --full) for stale indexes: picks
    up newly discovered sessions without a rebuild. Writes only CASS's own
    derived archive/index state; never touches projects, skills, or rules.
    """
    result = run_cass_json(["index", "--no-progress-events"],
                           timeout_s=timeout_s, machine_flag="--json")
    if not isinstance(result, dict):
        raise CassError("incremental index refresh returned unexpected output.")
    if not result.get("success", False):
        raise CassError("incremental index refresh reported success=false.",
                        str(result.get("error", ""))[-300:])
    return result
