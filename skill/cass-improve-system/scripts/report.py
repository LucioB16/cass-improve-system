"""Render findings JSON as a human-readable markdown review report.

Reads findings JSON (see analyze.py) from stdin/--input and writes markdown
to stdout (or --output). Excerpts are already redacted upstream; report.py
never invents evidence and states explicitly when nothing strong was found.
"""

from __future__ import annotations

import argparse
import json
import sys

TYPE_TITLES = {
    "user_correction": "Repeated user corrections",
    "error_mention": "Recurring errors",
    "retry_loop": "Debugging loops / retries",
    "manual_procedure": "Repeated manual procedures",
    "rediscovery": "Duplicated research / rediscovery",
    "success_pattern": "Successful reusable patterns",
}


def _read_stdin_text() -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read()
    return data.decode("utf-8") if isinstance(data, bytes) else data


def _finding_block(finding: dict) -> list[str]:
    title = TYPE_TITLES.get(finding["signal_type"], finding["signal_type"])
    lines = [
        f"### {title}",
        f"Type: {finding['signal_type']}",
        f"Destination: {finding['suggested_destination']}",
        f"Evidence: {finding['occurrences']} occurrence(s) across "
        f"{finding['session_count']} session(s), {finding['project_count']} project(s); "
        f"agents: {', '.join(finding['agents'])}; "
        f"cross-project: {'yes' if finding['cross_project'] else 'no'}",
        "Representative evidence:",
    ]
    for item in finding["evidence"]:
        lines.append(f"- `{item['ref']}`: {item['excerpt']}")
    lines += [
        "Why it matters: recurrence across sessions suggests a systemic gap "
        "rather than a one-off mistake; verify against the cited sessions before promoting.",
        "Proposed improvement: (agent fills in after reviewing evidence — "
        "draft the concrete rule, skill, or automation and request approval).",
        f"Confidence: {finding['confidence']}",
        "",
    ]
    return lines


def render(findings: dict) -> str:
    period = findings.get("period", {}).get("label", "?")
    coverage = findings.get("coverage", {})
    lines = [
        f"# System Review — {period}",
        "",
        "Coverage",
        f"- sessions discovered: {coverage.get('sessions_discovered', '?')}",
        f"- sessions analyzed: {coverage.get('sessions_analyzed', '?')}",
        f"- sessions skipped/failed: {coverage.get('sessions_skipped', '?')}",
        f"- agents: {', '.join(coverage.get('agents', [])) or '?'}",
        f"- projects/workspaces: {len(coverage.get('workspaces', []))} distinct",
        "",
        "## Executive summary",
        "",
    ]
    candidates = findings.get("candidates", [])
    watch = findings.get("watch_list", [])
    ignored = findings.get("ignored_single_occurrences", [])
    if not candidates and not watch:
        lines.append("No strong improvement candidates were found in this period. "
                     "Single-occurrence signals are listed under Do not promote.")
        lines.append("")
    else:
        lines.append(f"{len(candidates)} high-confidence and {len(watch)} "
                     "medium-confidence candidate(s). Details below.")
        lines.append("")
    lines.append("## High-confidence improvements")
    lines.append("")
    if candidates:
        for finding in candidates:
            lines.extend(_finding_block(finding))
    else:
        lines.append("None in this period.")
        lines.append("")
    for section, group in (("## Automation candidates", [f for f in candidates + watch
                             if "AUTOMATION" in f["suggested_destination"]]),
                           ("## New skill candidates", [f for f in candidates + watch
                             if "NEW SKILL" in f["suggested_destination"]]),
                           ("## Global-rule candidates", [f for f in candidates + watch
                             if "GLOBAL RULE" in f["suggested_destination"]]),
                           ("## Project-specific improvements", [f for f in candidates + watch
                             if "PROJECT" in f["suggested_destination"]])):
        lines.append(section)
        lines.append("")
        if group:
            for finding in group:
                lines.extend(_finding_block(finding))
        else:
            lines.append("None in this period.")
            lines.append("")
    lines.append("## Successful patterns worth preserving")
    lines.append("")
    successes = [f for f in candidates + watch if f["signal_type"] == "success_pattern"]
    if successes:
        for finding in successes:
            lines.extend(_finding_block(finding))
    else:
        lines.append("None detected in this period.")
        lines.append("")
    lines.append("## Low-confidence / watch list")
    lines.append("")
    if watch:
        for finding in watch:
            lines.extend(_finding_block(finding))
    else:
        lines.append("Nothing on the watch list.")
        lines.append("")
    lines.append("## Do not promote")
    lines.append("")
    if ignored:
        lines.append(f"{len(ignored)} single-occurrence signal(s) treated as anecdotes, "
                     "not promoted without further evidence.")
        lines.append("")
    else:
        lines.append("No isolated anecdotes recorded.")
        lines.append("")
    lines.append("## Coverage limitations")
    lines.append("")
    lines.append(f"- skipped/failed sessions: {coverage.get('sessions_skipped', 0)} "
                 "(exports that CASS could not provide; see normalization metadata).")
    lines.append("- heuristic signal extraction is approximate: every candidate "
                 "must be verified against its cited sessions before promotion.")
    lines.append("- report-only by default: no durable files were changed to produce this report.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render findings as markdown.")
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    try:
        if args.input:
            with open(args.input, encoding="utf-8") as fh:
                findings = json.load(fh)
        else:
            findings = json.loads(_read_stdin_text())
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"report: invalid findings input: {exc}", file=sys.stderr)
        return 2
    text = render(findings)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        # UTF-8 bytes: Windows consoles default to cp1252.
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
