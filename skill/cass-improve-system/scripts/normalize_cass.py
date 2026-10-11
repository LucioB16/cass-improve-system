#!/usr/bin/env python3
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
import unicodedata

from cass_adapter import CassError, export_session

MAX_CHARS_PER_MESSAGE = 1200
MAX_MESSAGES_PER_SESSION = 120
# Stratified retention budget (sums to MAX_MESSAGES_PER_SESSION): user turns
# and signal-bearing messages first, then head/tail context. Deterministic.
HEAD_KEEP = 15
TAIL_KEEP = 15
USER_KEEP = 50
SIGNAL_KEEP = 40


def infer_role(msg: dict) -> str:
    """Map vendor role/speaker fields to user|assistant|system|tool|?.

    Explicit `role` values pass through (with human/model/agent synonyms
    folded); Antigravity transcript records carry no `role` but do carry
    `source` (USER_EXPLICIT/MODEL) and `type` (USER_INPUT/PLANNER_RESPONSE),
    verified live against CASS 0.10.0 exports. Anything else stays `?` —
    roles are never guessed from message text.
    """
    raw = msg.get("role", "")
    if isinstance(raw, str) and raw.strip() and raw.strip().lower() not in ("?", "unknown"):
        folded = raw.strip().lower()
        if folded == "human":
            return "user"
        if folded in ("model", "agent"):
            return "assistant"
        return raw.strip()
    source = str(msg.get("source", "") or "").strip().upper()
    mtype = str(msg.get("type", "") or "").strip().upper()
    if source in ("USER_EXPLICIT", "USER", "HUMAN") or \
            mtype in ("USER_INPUT", "USER_MESSAGE", "HUMAN_TURN"):
        return "user"
    if source in ("MODEL", "ASSISTANT", "AGENT") or \
            mtype in ("PLANNER_RESPONSE", "ASSISTANT_MESSAGE",
                      "AGENT_RESPONSE", "MODEL_RESPONSE"):
        return "assistant"
    return "?"


def parse_time(value: object) -> str | None:
    """Parse epoch (s/ms/us) or ISO timestamps to UTC ISO strings. None if unusable."""
    from datetime import datetime, timezone
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and value > 0:
            v = float(value)
            if v > 1e14:
                v /= 1e6
            elif v > 1e11:
                v /= 1e3
            elif v < 1e9:
                return None
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        if isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None
    return None


def message_time(msg: dict) -> str | None:
    for key in ("timestamp", "created_at", "createdAt", "date", "time"):
        parsed = parse_time(msg.get(key))
        if parsed:
            return parsed
    return None


def _fold(text: str) -> str:
    """Accent/case-insensitive matching copy. Originals are kept for evidence."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


# Per-signal pattern groups. "en" preserves the original English behavior
# verbatim; "es" adds Spanish equivalents written UNACCENTED (matching runs
# on _fold()ed text). user_only signals only fire on user-role messages.
# Keep entries multi-word/anchored: single common words cause false positives.
_PATTERNS: dict[str, dict[str, object]] = {
    "user_correction": {
        "user_only": True,
        "en": [
            r"no[,!]?\s+(that's\s+)?wrong",
            r"no[,!]?\s+that is wrong",
            r"not\s+(quite\s+)?(right|correct)",
            r"you (misunderstood|missed)",
            r"actually,?\s+i (meant|wanted)",
            r"stop(,|\s).*do this instead",
            r"forget (it|that)",
            r"revert (that|it)",
            r"that's not what i (asked|meant)",
            r"wrong (file|approach|direction)",
        ],
        "es": [
            r"eso no es (lo que|asi|correcto)",
            r"no(,|\s)+(eso\s+)?no es (lo que|asi|correcto)",
            r"te (equivocas|equivocaste|confundiste)",
            r"estas mal",
            r"no(,|\s)+(era|queria|quiero|me referia|necesito)",
            r"me referia a",
            r"lo que (quiero|pedi|te pedi|necesito)",
            r"no te pedi",
            r"eso no te (pedi|pregunte)",
            r"por que (interrumpiste|paraste|pausaste|cortaste)",
            r"no\s+\w+\s+todavia",  # "no pases todavia": redirect, don't proceed
            r"recorda( que|te|r que)",
            r"acordate( que| de)",
            r"vos (antes|me habias|dijiste|decis)",
        ],
    },
    "error_mention": {
        "user_only": False,
        "en": [
            r"error|failed|failure|traceback|exception|panic|timeout|timed out|"
            r"not found|denied|refused|broken|crash(?:ed|es)?",
        ],
        "es": [
            r"fallo|falla|fallen|fallaron|fallido|excepcion|roto|se rompio|"
            r"denegado|no encontrado|colgo|cuelga|tildo|no responde",
        ],
    },
    "retry_loop": {
        "user_only": False,
        "en": [
            r"try again|retry|still (failing|broken|not working)|same error|again\?",
        ],
        "es": [
            r"sigue (sin funcionar|fallando|roto|igual|pasando)",
            r"todavia (no funciona|falla|roto|igual)",
            r"el mismo error",
            r"otra vez|de nuevo",
            r"reintent\w*",
            r"intentalo (de nuevo|otra vez)",
        ],
    },
    "manual_procedure": {
        "user_only": False,
        "en": [
            r"manually|manually (copy|run|edit|check)|boilerplate|"
            r"every time i|each time|repetitive|tedious",
        ],
        "es": [
            r"manualmente|a mano|cada vez (que|tengo|hay)|todas las veces|"
            r"siempre (tengo|hay) que|repetitivo|tedioso",
        ],
    },
    "rediscovery": {
        "user_only": True,
        "en": [
            r"let me (look up|check the docs|search for)",
            r"how (do|does|can) (i|you).*\?",
            r"what('s| is) the (correct|right|best).*(syntax|api|flag|option)",
        ],
        "es": [
            r"como (hago|puedo|se hace|funciona|uso|configuro)",
            r"cual es (la forma|el comando|la sintaxis|la manera)",
            r"donde (esta|queda)",
            r"que (significa|hace|es) est[eo]",
            r"para que sirve",
        ],
    },
    "success_pattern": {
        "user_only": False,
        "en": [
            r"(that|this) worked|perfect,?\s*thanks|exactly what i (wanted|needed)|"
            r"nice(,\s*that)? (worked|fixed)|shipped|merged",
        ],
        "es": [
            r"funciono|perfecto|genial|excelente|buenisimo",
            r"eso era( lo que| exactamente)?|era (eso|justo lo que)",
            r"quedo (bien|perfecto|genial)",
            r"listo,?\s*gracias",
        ],
    },
}

_COMPILED: dict[str, list] = {
    name: [re.compile(r"\b(?:" + p + r")", re.I) for p in defs["en"] + defs["es"]]  # type: ignore[operator]
    for name, defs in _PATTERNS.items()
}
_USER_ONLY = {name for name, defs in _PATTERNS.items() if defs["user_only"]}

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
    is_user = (role == "user")
    folded = _fold(text or "")
    raw = text or ""
    found = []
    for stype, patterns in _COMPILED.items():
        if stype in _USER_ONLY and not is_user:
            continue
        if any(p.search(folded) for p in patterns):
            found.append({"type": stype, "excerpt": _excerpt(raw[:400]), "ref": ref})
    return found


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


def _stratified_indices(n: int, user_idx: list[int], signal_idx: list[int]) -> list[int]:
    """Deterministic bounded retention: signal-bearing + user turns first,
    then head/tail context. Returns sorted original indices."""
    if n <= MAX_MESSAGES_PER_SESSION:
        return list(range(n))
    chosen: list[int] = []
    seen: set[int] = set()

    def take(indices: list[int], budget: int) -> None:
        for i in indices:
            if len(chosen) >= MAX_MESSAGES_PER_SESSION or budget <= 0:
                return
            if i not in seen and 0 <= i < n:
                seen.add(i)
                chosen.append(i)
                budget -= 1

    take(signal_idx, SIGNAL_KEEP)
    take(user_idx, USER_KEEP)
    take(list(range(HEAD_KEEP)), HEAD_KEEP)
    take(list(range(max(0, n - TAIL_KEEP), n)), TAIL_KEEP)
    return sorted(chosen)


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
        "metadata": {"truncated": False, "export_failed": False,
                     "recovered_from_panic": False, "sampling": "none",
                     "total_messages": 0, "retained_messages": 0,
                     "activity_start": None, "activity_end": None,
                     "timestamps": "none", "out_of_period": False},
    }
    try:
        raw, recovered = export_session(
            session_id, entry.get("source_id", "local"), timeout_s=timeout_s)
        session["metadata"]["recovered_from_panic"] = recovered
    except CassError:
        session["metadata"]["export_failed"] = True
        return session
    raw = [m for m in raw if isinstance(m, dict)]
    total = len(raw)
    session["metadata"]["total_messages"] = total
    # Full scan for signals FIRST so late-session behavior is discoverable;
    # retention below is only about which message bodies are kept.
    scanned: list[tuple[int, str, str]] = []  # (index, role, text)
    user_idx: list[int] = []
    for i, msg in enumerate(raw):
        role = infer_role(msg)
        content = msg.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content)[:4000]
        scanned.append((i, role, text))
        if role == "user":
            user_idx.append(i)
    ordered_signals: list[dict] = []
    signal_idx: list[int] = []
    for i, role, text in scanned:
        found = extract_signals(role, text, f"{session_id}#msg{i}")
        if found:
            ordered_signals.append((i, found))
            signal_idx.append(i)
    session["signals"] = [s for _, group in ordered_signals for s in group]
    # Activity range from message timestamps, else inventory fields.
    stamps = [t for _, _, t in
              ((i, r, message_time(raw[i])) for i, r, _ in scanned) if t]
    if stamps:
        session["metadata"]["activity_start"] = min(stamps)
        session["metadata"]["activity_end"] = max(stamps)
        session["metadata"]["timestamps"] = "messages"
    else:
        bounds = [parse_time(entry.get(k)) for k in
                  ("started_at", "ended_at", "modified")]
        bounds = [b for b in bounds if b]
        if bounds:
            session["metadata"]["activity_start"] = min(bounds)
            session["metadata"]["activity_end"] = max(bounds)
            session["metadata"]["timestamps"] = "inventory"
    session["metadata"]["out_of_period"] = _outside_period(
        session["metadata"]["activity_start"],
        session["metadata"]["activity_end"],
        entry.get("_period_start"), entry.get("_period_end"))
    # Bounded stratified retention of message bodies.
    keep = _stratified_indices(total, user_idx, signal_idx)
    if len(keep) < total:
        session["metadata"]["truncated"] = True
        session["metadata"]["sampling"] = (
            "stratified: signal-bearing + user turns first, then head/tail")
    session["metadata"]["retained_messages"] = len(keep)
    by_index = {i: (role, text) for i, role, text in scanned}
    for i in keep:
        role, text = by_index[i]
        session["messages"].append({"role": role, "excerpt": _excerpt(text)})
        tools = raw[i].get("tool_calls") or raw[i].get("toolCalls") or []
        if isinstance(tools, list):
            for call in tools[:20]:
                name = call.get("name") if isinstance(call, dict) else str(call)
                session["tool_calls"].append(str(name)[:120])
    return session


def _outside_period(start: str | None, end: str | None,
                    bound_start: str | None, bound_end: str | None) -> bool:
    """True only when a reliable activity range sits fully outside the
    requested bounds. Missing data never excludes: uncertainty is reported,
    not filtered."""
    if not start or not end or not bound_start or not bound_end:
        return False
    return end < bound_start or start > bound_end


def normalize_inventory(inventory: dict, timeout_s: int = 120) -> dict:
    sessions = [normalize_one(e, timeout_s) for e in inventory.get("sessions", [])]
    ok = [s for s in sessions if not s["metadata"]["export_failed"]]
    failed = len(sessions) - len(ok)
    recovered = sum(1 for s in ok if s["metadata"].get("recovered_from_panic"))
    outside = sum(1 for s in ok if s["metadata"].get("out_of_period"))
    unknown_ts = sum(1 for s in ok if s["metadata"].get("timestamps") == "none")
    return {
        "period": inventory.get("period", {}),
        "sessions": sessions,
        "coverage": {
            **inventory.get("coverage", {}),
            "sessions_discovered": len(sessions),
            "sessions_analyzed": len(ok),
            "sessions_skipped": failed,
            "sessions_recovered": recovered,
            "sessions_outside_period": outside,
            "sessions_timestamp_unknown": unknown_ts,
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
