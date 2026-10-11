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


def confidence(sessions: int, projects: int, occurrences: int, stype: str) -> str:
    if sessions >= 3 and projects >= 2:
        return "high"
    if sessions >= 2 or (stype == "user_correction" and occurrences >= 2):
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


def analyze(normalized: dict) -> dict:
    sessions = normalized.get("sessions", [])
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
                "excerpts": [],
            })
            bucket["occurrences"] += 1
            bucket["session_ids"].add(sid)
            bucket["agents"].add(str(session.get("agent", "?")))
            bucket["workspaces"].add(str(session.get("workspace", "?")))
            if len(bucket["excerpts"]) < 3:
                bucket["excerpts"].append({
                    "ref": signal.get("ref", sid),
                    "excerpt": signal.get("excerpt", "")[:300],
                })
    candidates, watch, ignored = [], [], []
    for (stype, fp), bucket in clusters.items():
        n_sessions = len(bucket["session_ids"])
        n_projects = len(bucket["workspaces"])
        conf = confidence(n_sessions, n_projects, bucket["occurrences"], stype)
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
