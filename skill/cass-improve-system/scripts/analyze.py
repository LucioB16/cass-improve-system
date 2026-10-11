#!/usr/bin/env python3
"""Aggregate per-session signals into scored cross-session candidates.

Reads normalized sessions JSON (see normalize_cass.py) from stdin/--input,
clusters signals by (type, fingerprint), and emits findings JSON:
  {"period", "coverage", "candidates": [...], "watch_list": [...], "ignored": [...]}
See references/promotion-policy.md for thresholds and destinations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

_WORD = re.compile(r"[a-z0-9]{3,}")

_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "you", "your", "are",
    "was", "were", "have", "has", "had", "not", "but", "from", "they",
    "them", "then", "than", "when", "what", "which", "will", "would",
    "there", "their", "about", "into", "could", "should", "just",
})

TYPE_TITLES = {
    "user_correction": "Repeated user corrections",
    "error_mention": "Recurring errors",
    "retry_loop": "Debugging loops / retries",
    "manual_procedure": "Repeated manual procedures",
    "rediscovery": "Duplicated research / rediscovery",
    "success_pattern": "Successful reusable patterns",
}

DESTINATIONS = {
    "user_correction": ["PROJECT RULE", "GLOBAL RULE (if cross-project)"],
    "error_mention": ["TOOLING GAP", "PROJECT KNOWLEDGE"],
    "retry_loop": ["AUTOMATION", "EXISTING SKILL improvement"],
    "manual_procedure": ["AUTOMATION", "NEW SKILL"],
    "rediscovery": ["PROJECT KNOWLEDGE", "NEW SKILL"],
    "success_pattern": ["PRESERVE (document as reusable pattern)"],
}


def fingerprint(text: str) -> str:
    """Coarse content fingerprint: top keywords, order-independent."""
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=lambda w: (-freq[w], w))[:8]
    return hashlib.sha1(" ".join(sorted(top)).encode()).hexdigest()[:12]


def confidence(n_sessions: int, n_projects: int, occurrences: int,
               stype: str, has_user_evidence: bool,
               corroborated: bool = False, templated: bool = False) -> str:
    """Provenance-aware confidence.

    User-backed findings use the standard thresholds. Findings with no
    user-authored evidence are capped: only systemic spread (3+ sessions
    across 2+ projects) WITH user corroboration in a member session
    (a user error/correction/retry report alongside the tool evidence)
    reaches medium; pure echoed/diagnostic repetition stays low.
    Byte-identical LONG excerpts repeated across sessions (pasted
    templates, echoed files) cap at low; short habitual utterances are
    exempt from the template rule.
    """
    if templated:
        return "low"
    if has_user_evidence:
        if n_sessions >= 3 and n_projects >= 2:
            return "high"
        if n_sessions >= 2 or (stype == "user_correction" and occurrences >= 2):
            return "medium"
        return "low"
    if corroborated and n_sessions >= 3 and n_projects >= 2:
        return "medium"
    return "low"


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


def _msg_index(ref: object) -> int:
    """Numeric original message index from a session ref (s#msg100).
    Lexicographic ordering would rank msg100 below msg9; numeric is correct."""
    match = re.search(r"#msg(\d+)$", str(ref or ""))
    return int(match.group(1)) if match else -1


def synthesize_session_retries(sessions: list[dict]) -> int:
    """Session-level retry evidence, scoped by period class.

    The same error fingerprint recurring in 3+ distinct messages means
    repeated unsuccessful attempts — but only within the period class that
    actually contains the recurrence. A pre_window-only group synthesizes a
    pre_window retry (historical context, never current-period evidence);
    mixed groups (e.g. 2 pre + 1 in) synthesize nothing, because no single
    period class satisfies the recurrence condition. Legacy unbounded
    evidence groups together for backward compatibility.
    """
    made = 0
    for session in sessions:
        by_key: dict[tuple[str, str], list[dict]] = {}
        for signal in session.get("signals", []):
            if signal.get("type") != "error_mention":
                continue
            period = signal.get("period", "unbounded")
            by_key.setdefault(
                (fingerprint(signal.get("excerpt", "")), period), []).append(signal)
        for (_, period), group in by_key.items():
            refs = {g.get("ref") for g in group}
            if len(group) >= 3 and len(refs) >= 3:
                latest = max(group, key=lambda g: _msg_index(g.get("ref")))
                exists = any(s.get("type") == "retry_loop"
                             and s.get("synthesized") == "session-recurrence"
                             and s.get("ref") == latest.get("ref")
                             and s.get("period") == period
                             for s in session.get("signals", []))
                if exists:
                    continue  # idempotent: already synthesized for this group
                session["signals"].append({
                    "type": "retry_loop",
                    "excerpt": latest.get("excerpt", "")[:300],
                    "ref": latest.get("ref", session.get("session_id", "?")),
                    "prov": latest.get("prov", "unknown"),
                    "period": period,
                    "synthesized": "session-recurrence",
                    "source_count": len(refs),
                })
                made += 1
    return made


def analyze(normalized: dict) -> dict:
    sessions = normalized.get("sessions", [])
    synthesize_session_retries(sessions)
    # Sessions carrying user-authored error/correction/retry reports, for
    # corroborating tool-evidence clusters found in the same session.
    session_user_reports: dict[str, bool] = {}
    for session in sessions:
        sid = session.get("session_id", "?")
        session_user_reports[sid] = any(
            s.get("prov") == "user_text" and s.get("type") in
            ("error_mention", "user_correction", "retry_loop")
            for s in session.get("signals", []))
    clusters: dict[tuple[str, str], dict] = {}
    for session in sessions:
        sid = session.get("session_id", "?")
        for signal in session.get("signals", []):
            stype = signal.get("type", "?")
            key = (stype, fingerprint(signal.get("excerpt", "")))
            bucket = clusters.setdefault(key, {
                "type": stype,
                "occurrences": 0,
                "session_ids": set(),
                "agents": set(),
                "workspaces": set(),
                "prov_counts": {},
                "bounded": False,
                "in_window": 0,
                "sessions_in": set(),
                "projects_in": set(),
                "user_in": 0,
                "pre_window": 0,
                "unknown_time": 0,
                "distinct_excerpts": set(),
                "corroborated": False,
                "excerpts": [],
            })
            period = signal.get("period", "unbounded")
            in_scope = period in ("in_window", "unbounded")
            bucket["occurrences"] += 1
            bucket["session_ids"].add(sid)
            bucket["agents"].add(str(session.get("agent", "?")))
            bucket["workspaces"].add(str(session.get("workspace", "?")))
            prov = signal.get("prov", "unknown")
            bucket["prov_counts"][prov] = bucket["prov_counts"].get(prov, 0) + 1
            if period != "unbounded":
                bucket["bounded"] = True
            if period == "in_window" or period == "unbounded":
                bucket["in_window"] += 1
                bucket["sessions_in"].add(sid)
                bucket["projects_in"].add(str(session.get("workspace", "?")))
                if prov == "user_text":
                    bucket["user_in"] += 1
            elif period == "pre_window":
                bucket["pre_window"] += 1
            else:
                bucket["unknown_time"] += 1
            if len(bucket["excerpts"]) < 3:
                bucket["excerpts"].append({
                    "ref": signal.get("ref", sid),
                    "excerpt": signal.get("excerpt", "")[:300],
                })
            bucket["distinct_excerpts"].add(signal.get("excerpt", ""))
            if session_user_reports.get(sid):
                bucket["corroborated"] = True
    candidates, watch, ignored = [], [], []
    for (stype, fp), bucket in clusters.items():
        if bucket["bounded"]:
            # Real period bounds: only in-window evidence promotes.
            n_sessions = len(bucket["sessions_in"])
            n_projects = len(bucket["projects_in"])
            occ_in = bucket["in_window"]
        else:
            # Legacy inputs without period metadata count fully.
            n_sessions = len(bucket["session_ids"])
            n_projects = len(bucket["workspaces"])
            occ_in = bucket["occurrences"]
        has_user = (bucket["prov_counts"].get("user_text", 0) > 0
                    and bucket["user_in"] > 0)
        templated = (len(bucket["distinct_excerpts"]) <= 1
                     and len(bucket["session_ids"]) >= 2
                     and max((len(e) for e in bucket["distinct_excerpts"]),
                             default=0) > 120)
        conf = confidence(n_sessions, n_projects, occ_in, stype, has_user,
                          bucket["corroborated"], templated)
        first_words = " ".join(bucket["excerpts"][0]["excerpt"].split()[:8]) \
            if bucket["excerpts"] else ""
        finding = {
            "id": f"{stype}:{fp[:8]}",
            "title": f"{TYPE_TITLES.get(stype, stype)} — {first_words}".rstrip(" —"),
            "signal_type": stype,
            "occurrences": bucket["occurrences"],
            "session_count": n_sessions,
            "project_count": n_projects,
            "agents": sorted(bucket["agents"]),
            "cross_project": n_projects >= 2,
            "confidence": conf,
            "destinations": DESTINATIONS.get(stype, ["EPHEMERAL"]),
            "provenance": dict(sorted(bucket["prov_counts"].items())),
            "evidence_in_window": bucket["in_window"],
            "evidence_pre_window": bucket["pre_window"],
            "evidence_unknown_time": bucket["unknown_time"],
            "related_ids": [],
            "evidence": bucket["excerpts"],
        }
        if conf == "high":
            candidates.append(finding)
        elif conf == "medium":
            watch.append(finding)
        else:
            ignored.append(finding)
    rank = {"high": 0, "medium": 1, "low": 2}
    for group in (candidates, watch, ignored):
        group.sort(key=lambda f: (rank[f["confidence"]], -f["session_count"], -f["project_count"]))
    # Event grouping: findings sharing an evidence fingerprint (different
    # signal types, same underlying event) link to each other. IDs stay
    # stable and unique; the report shows the relation without re-rendering.
    by_fp: dict[str, list[dict]] = {}
    for finding in candidates + watch + ignored:
        fp = finding["id"].split(":", 1)[1]
        by_fp.setdefault(fp, []).append(finding)
    for group in by_fp.values():
        if len(group) > 1:
            ids = sorted(f["id"] for f in group)
            for finding in group:
                finding["related_ids"] = [i for i in ids if i != finding["id"]]
    return {
        "period": normalized.get("period", {}),
        "coverage": normalized.get("coverage", {}),
        "candidates": candidates,
        "watch_list": watch,
        "ignored_single_occurrences": ignored,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate signals into findings.")
    parser.add_argument("--input", default=None)
    args = parser.parse_args(argv)
    try:
        if args.input:
            with open(args.input, encoding="utf-8") as fh:
                normalized = json.load(fh)
        else:
            normalized = json.loads(_read_stdin_text())
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"analyze: invalid normalized input: {exc}", file=sys.stderr)
        return 2
    # UTF-8 bytes: Windows consoles default to cp1252.
    sys.stdout.buffer.write(
        json.dumps(analyze(normalized), indent=2, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
