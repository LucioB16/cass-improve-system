#!/usr/bin/env python3
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
    if isinstance(data, bytes):
        # Tolerate UTF-16 piped input (e.g. PowerShell `>` redirection
        # produces UTF-16LE); the pipeline itself always emits UTF-8.
        if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (
                b"\x00" in data[:100] and b"\x00" in data[1:100]):
            return data.decode("utf-16")
        return data.decode("utf-8")
    return data


def _finding_block(finding: dict) -> list[str]:
    title = finding.get("title") or TYPE_TITLES.get(
        finding["signal_type"], finding["signal_type"])
    destinations = finding.get("destinations") or \
        ([finding["suggested_destination"]] if finding.get("suggested_destination") else [])
    lines = [
        f"### {title} `[{finding.get('id', '?')}]`",
        f"Type: {finding['signal_type']}",
        f"Destinations: {', '.join(destinations) or '?'}",
        f"Evidence: {finding['occurrences']} occurrence(s) across "
        f"{finding['session_count']} session(s), {finding['project_count']} project(s); "
        f"agents: {', '.join(finding['agents'])}; "
        f"cross-project: {'yes' if finding['cross_project'] else 'no'}",
        "Representative evidence:",
    ]
    for item in finding["evidence"]:
        lines.append(f"- `{item['ref']}`: {item['excerpt']}")
    related = finding.get("related_ids") or []
    if related:
        lines.append(f"Related signals (same evidence event): {', '.join(f'`{r}`' for r in related)}")
    lines += [
        "Why it matters: recurrence across sessions suggests a systemic gap "
        "rather than a one-off mistake; verify against the cited sessions before promoting.",
        "Proposed improvement: (agent fills in after reviewing evidence — "
        "draft the concrete rule, skill, or automation and request approval).",
        f"Confidence: {finding['confidence']}",
        "",
    ]
    return lines


def _destination_index(grouped: list[dict]) -> list[str]:
    """Title-only cross-reference: each finding rendered once above, indexed
    here by destination without repeating evidence."""
    lines = ["## Index by destination", ""]
    by_dest: dict[str, list[dict]] = {}
    for finding in grouped:
        destinations = finding.get("destinations") or \
            ([finding["suggested_destination"]] if finding.get("suggested_destination") else ["?"])
        for dest in destinations:
            by_dest.setdefault(dest, []).append(finding)
    if not by_dest:
        lines.append("No findings to index.")
        lines.append("")
        return lines
    for dest in sorted(by_dest):
        lines.append(f"### {dest}")
        for finding in by_dest[dest]:
            title = finding.get("title", finding.get("signal_type", "?"))
            lines.append(f"- {title} `[{finding.get('id', '?')}]` "
                         f"({finding['confidence']}, "
                         f"{finding['session_count']} sessions)")
        lines.append("")
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
        f"- sessions outside requested period (kept, flagged): "
        f"{coverage.get('sessions_outside_period', 0)}",
        f"- sessions without usable timestamps: "
        f"{coverage.get('sessions_timestamp_unknown', 0)}",
        f"- signals by type: "
        f"{', '.join(f'{k}={v}' for k, v in coverage.get('signals_by_type', {}).items()) or '?'}",
        f"- signals by provenance: "
        f"{', '.join(f'{k}={v}' for k, v in coverage.get('signals_by_provenance', {}).items()) or '?'}",
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
    # Category sections are title-only cross-references: every finding is
    # rendered in full exactly once (above); destinations live in the finding.
    def _dest_matches(finding: dict, needle: str) -> bool:
        destinations = finding.get("destinations") or \
            ([finding["suggested_destination"]] if finding.get("suggested_destination") else [])
        return any(needle in d for d in destinations)

    for section, needle in (("## Automation candidates", "AUTOMATION"),
                            ("## New skill candidates", "NEW SKILL"),
                            ("## Global-rule candidates", "GLOBAL RULE"),
                            ("## Project-specific improvements", "PROJECT")):
        group = [f for f in candidates + watch if _dest_matches(f, needle)]
        lines.append(section)
        lines.append("")
        if group:
            for finding in group:
                title = finding.get("title", finding["signal_type"])
                lines.append(f"- {title} `[{finding.get('id', '?')}]` "
                             "(full block under High-confidence improvements / watch list)")
        else:
            lines.append("None in this period.")
        lines.append("")
    lines.extend(_destination_index(candidates + watch))
    lines.append("## Successful patterns worth preserving")
    lines.append("")
    successes = [f for f in candidates + watch if f["signal_type"] == "success_pattern"]
    if successes:
        # Title-only cross-references: full blocks already rendered above.
        for finding in successes:
            title = finding.get("title", finding["signal_type"])
            lines.append(f"- {title} `[{finding.get('id', '?')}]`")
        lines.append("")
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
    recovered = coverage.get("sessions_recovered", 0)
    if recovered:
        lines.append(f"- sessions recovered from nonzero-exit CASS exports: {recovered} "
                     "(complete `-o` output salvaged after an upstream WAL/panic; "
                     "validated as parseable JSON before acceptance).")
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
