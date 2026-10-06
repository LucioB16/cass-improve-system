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
import sys

from cass_adapter import CassError, list_sessions
from period import parse_period


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
    agents_seen = sorted({str(s.get("agent") or "?") for s in sessions})
    workspaces_seen = sorted({str(s.get("workspace") or "?") for s in sessions})
    return {
        "period": {"label": label, "since": since, "until": until},
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
