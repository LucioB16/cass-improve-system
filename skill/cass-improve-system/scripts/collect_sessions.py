#!/usr/bin/env python3
"""Enumerate the review corpus for a period via CASS (read-only).

Usage:
  python collect_sessions.py --period "last 24h" [--agent X] [--workspace Y]
                             [--limit 200] [--timeout 120]

Writes an inventory JSON object to stdout:
  {"period": {...}, "sessions": [...], "coverage": {...}}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from cass_adapter import CassError, list_sessions
from period import parse_period


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_bound(value: str | None, is_end: bool, now: datetime | None = None) -> str | None:
    """Resolve a CASS --since/--until value to an UTC ISO bound for
    period-fidelity comparison. Approximations (UTC day for keywords) are
    documented; bounds only ever FLAG sessions, never exclude them."""
    if value is None:
        return None
    now = now or _utcnow()
    text = value.strip().lower()
    m = re.fullmatch(r"-(\d+)([mhdw])", text)
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount),
                 "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
        return (now - delta).isoformat()
    if text in ("today",):
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if text in ("yesterday",):
        day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return (day + timedelta(days=1) - timedelta(microseconds=1)).isoformat() if is_end \
            else day.isoformat()
    if text in ("week", "last week"):
        return (now - timedelta(days=7)).isoformat()
    try:
        stamp = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
            if is_end:
                parsed += timedelta(days=1) - timedelta(microseconds=1)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def collect(
    period_text: str | None,
    since: str | None,
    until: str | None,
    agents: list[str],
    workspaces: list[str],
    limit: int,
    timeout_s: int,
) -> dict:
    if period_text:
        since, until, label = parse_period(period_text)
    else:
        label = f"since {since}" + (f" until {until}" if until else "")
    # NOTE: `cass sessions` supports --since but not --until on all versions;
    # until is applied as a client-side filter below when present.
    sessions = list_sessions(
        since=since, limit=limit, agent=agents or None,
        workspace=workspaces or None, timeout_s=timeout_s,
    )
    if until:
        sessions = [s for s in sessions if _session_on_or_before(s, until)]
    bound_start = resolve_bound(since, is_end=False)
    bound_end = resolve_bound(until, is_end=True) or _utcnow().isoformat()
    for s in sessions:
        s["_period_start"] = bound_start
        s["_period_end"] = bound_end
    agents_seen = sorted({str(s.get("agent") or "?") for s in sessions})
    workspaces_seen = sorted({str(s.get("workspace") or "?") for s in sessions})
    return {
        "period": {"label": label, "since": since, "until": until,
                   "bounds_start": bound_start, "bounds_end": bound_end},
        "sessions": sessions,
        "coverage": {
            "sessions_discovered": len(sessions),
            "agents": agents_seen,
            "workspaces": workspaces_seen,
        },
    }


def _session_on_or_before(session: dict, until: str) -> bool:
    stamp = session.get("modified") or session.get("started_at") or ""
    return str(stamp)[:10] <= until


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect the session inventory.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--period", help='e.g. "last 24h", "last 7 days", "2026-10-01..2026-10-06"')
    group.add_argument("--since", help="CASS --since value used directly")
    parser.add_argument("--until", default=None)
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--workspace", action="append", default=[])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        inventory = collect(args.period, args.since, args.until,
                            args.agent, args.workspace, args.limit, args.timeout)
    except (CassError, ValueError) as exc:
        print(f"collect_sessions: {exc}", file=sys.stderr)
        return 2
    # UTF-8 bytes: Windows consoles default to cp1252 and session content
    # routinely contains emoji/CJK that cp1252 cannot encode.
    sys.stdout.buffer.write(
        json.dumps(inventory, indent=2, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
