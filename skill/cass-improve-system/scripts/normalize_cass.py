"""Normalize CASS session exports into a vendor-independent schema.

Reads an inventory JSON (see collect_sessions.py) from stdin or --input,
exports each session via the adapter, and writes normalized sessions to
stdout. Content is truncated per message/session to bound context size,
and secret-looking values are redacted (see redact()).

NormalizedSession schema:
  session_id, agent, workspace, source, started_at, ended_at,
  message_count, messages[{role, excerpt}], tool_calls[], tool_results[],
  signals[{type, excerpt, ref}], metadata{truncated, export_failed}
Signal types are heuristic DERIVED labels (see references/analysis-rubric.md),
never presented as CASS-provided fields.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from cass_adapter import CassError, export_session

MAX_CHARS_PER_MESSAGE = 1200
MAX_MESSAGES_PER_SESSION = 120

_CORRECTION = re.compile(
    r"\b(no[,!]?\s+(that's\s+)?wrong|not\s+(quite\s+)?(right|correct)|"
    r"you (misunderstood|missed)|actually,?\s+i (meant|wanted)|"
    r"stop(,|\s).*do this instead|forget (it|that)|revert (that|it)|"
    r"that's not what i (asked|meant)|wrong (file|approach|direction))\b",
    re.I,
)
_ERROR = re.compile(
    r"\b(error|failed|failure|traceback|exception|panic|timeout|timed out|"
    r"not found|denied|refused|broken|crash(?:ed|es)?)\b",
    re.I,
)
_RETRY_LOOP = re.compile(r"\b(try again|retry|still (failing|broken|not working)|"
                         r"same error|again\?)\b", re.I)
_MANUAL = re.compile(
    r"\b(manually|manually (copy|run|edit|check)|boilerplate|"
    r"every time i|each time|repetitive|tedious)\b",
    re.I,
)
_RESEARCH = re.compile(
    r"\b(let me (look up|check the docs|search for)|how (do|does|can) (i|you).*\?|"
    r"what('s| is) the (correct|right|best).*(syntax|api|flag|option))\b",
    re.I,
)
_SUCCESS = re.compile(
    r"\b((that|this) worked|perfect,?\s*thanks|exactly what i (wanted|needed)|"
    r"nice(,\s*that)? (worked|fixed)|shipped|merged)\b",
    re.I,
)

# Secret-looking patterns -> redaction label.
_SECRETS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{10,}"), "[REDACTED:github-token]"),
    (re.compile(r"sk-(proj-)?[A-Za-z0-9\-_]{10,}"), "[REDACTED:api-key]"),
    (re.compile(r"xox[bpars]-[A-Za-z0-9\-]+"), "[REDACTED:slack-token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|passwd|password|bearer)\s*[:=]\s*\S+"),
     "[REDACTED:credential]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED:private-key]"),
    (re.compile(r"(?i)authorization:\s*\S+"), "Authorization: [REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def _excerpt(text: str) -> str:
    text = redact(text or "")
    if len(text) > MAX_CHARS_PER_MESSAGE:
        return text[:MAX_CHARS_PER_MESSAGE] + "…[truncated]"
    return text


def extract_signals(role: str, text: str, ref: str) -> list[dict]:
    """Heuristic per-message signal detection. All matches are DERIVED."""
    if role != "user":
        user_correction = False
    else:
        user_correction = True
    found = []
    lowered = text or ""
    if _CORRECTION.search(lowered) and user_correction:
        found.append({"type": "user_correction", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    if _ERROR.search(lowered):
        found.append({"type": "error_mention", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    if _RETRY_LOOP.search(lowered):
        found.append({"type": "retry_loop", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    if _MANUAL.search(lowered):
        found.append({"type": "manual_procedure", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    if _RESEARCH.search(lowered) and user_correction:
        found.append({"type": "rediscovery", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    if _SUCCESS.search(lowered):
        found.append({"type": "success_pattern", "excerpt": _excerpt(lowered[:400]), "ref": ref})
    return found


def _read_stdin_text() -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read()
    return data.decode("utf-8") if isinstance(data, bytes) else data


def normalize_one(entry: dict, timeout_s: int = 120) -> dict:
    session_id = str(entry.get("path") or entry.get("id") or "?")
    session = {
        "session_id": session_id,
        "agent": entry.get("agent", "?"),
        "workspace": entry.get("workspace", "?"),
        "source": entry.get("source_id", "?"),
        "started_at": entry.get("started_at"),
        "ended_at": entry.get("ended_at") or entry.get("modified"),
        "message_count": entry.get("message_count"),
        "messages": [],
        "tool_calls": [],
        "tool_results": [],
        "signals": [],
        "metadata": {"truncated": False, "export_failed": False},
    }
    try:
        raw = export_session(session_id, entry.get("source_id", "local"), timeout_s=timeout_s)
    except CassError:
        session["metadata"]["export_failed"] = True
        return session
    if len(raw) > MAX_MESSAGES_PER_SESSION:
        session["metadata"]["truncated"] = True
    for i, msg in enumerate(raw[:MAX_MESSAGES_PER_SESSION]):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "?"))
        content = msg.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content)[:4000]
        session["messages"].append({"role": role, "excerpt": _excerpt(text)})
        ref = f"{session_id}#msg{i}"
        session["signals"].extend(extract_signals(role, text, ref))
        tools = msg.get("tool_calls") or msg.get("toolCalls") or []
        if isinstance(tools, list):
            for call in tools[:20]:
                name = call.get("name") if isinstance(call, dict) else str(call)
                session["tool_calls"].append(str(name)[:120])
    return session


def normalize_inventory(inventory: dict, timeout_s: int = 120) -> dict:
    sessions = [normalize_one(e, timeout_s) for e in inventory.get("sessions", [])]
    ok = [s for s in sessions if not s["metadata"]["export_failed"]]
    failed = len(sessions) - len(ok)
    return {
        "period": inventory.get("period", {}),
        "sessions": sessions,
        "coverage": {
            **inventory.get("coverage", {}),
            "sessions_discovered": len(sessions),
            "sessions_analyzed": len(ok),
            "sessions_skipped": failed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize CASS exports.")
    parser.add_argument("--input", default=None, help="inventory JSON file (default: stdin)")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.input:
            with open(args.input, encoding="utf-8") as fh:
                inventory = json.load(fh)
        else:
            inventory = json.loads(_read_stdin_text())
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"normalize: invalid inventory input: {exc}", file=sys.stderr)
        return 2
    # UTF-8 bytes: Windows consoles default to cp1252 and session content
    # routinely contains emoji/CJK that cp1252 cannot encode.
    sys.stdout.buffer.write(
        json.dumps(normalize_inventory(inventory, args.timeout),
                   indent=2, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
