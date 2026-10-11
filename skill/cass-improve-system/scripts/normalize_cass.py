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
    if not isinstance(msg, dict):
        return "?"
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
    if source == "SYSTEM" or mtype in ("SYSTEM_MESSAGE", "CHECKPOINT"):
        return "system"
    # GitHub Copilot Chronicle events (verified: CASS `export --format json`
    # returns these raw, with message payloads nested under `data`).
    # Dotted lowercase discriminators; matched case-insensitively on the
    # exact known set only — unknown future event types stay `?`.
    ctype = str(msg.get("type", "") or "").strip().lower()
    if ctype in ("user.message", "userpromptsubmitted"):
        return "user"
    if ctype in ("assistant.message", "assistantresponse"):
        return "assistant"
    if ctype in ("tool.execution_start", "tool.execution_complete"):
        return "tool"
    if ctype in ("system.message", "session.start", "session.model_change",
                 "session.task_complete", "session.shutdown",
                 "assistant.turn_start", "assistant.turn_end"):
        return "system"
    return "?"


# Envelope markers are structural framing, not prose: tool executions and
# injected context arrive wrapped in them (verified: 404/638 assistant
# messages in live OpenCode exports open with [Tool Output]).
_TOOL_ENVELOPES = ("[tool output]", "<tool_result>", "<toolresult>")
_SYSTEM_ENVELOPES = ("[context]", "<context>", "[system]")

# Injected harness directives: a message OPENING with one of these markers
# is framework text even when the harness presents role=user (verified live:
# OH-MY-OPENCODE TODO-continuation directives open with "[SYSTEM DIRECTIVE:").
# Leading-position only: a user merely DISCUSSING directives mid-message
# stays user-authored. <auto-slash-command> is deliberately NOT here: those
# blocks wrap the user's own goal text.
_INJECTED_OPENERS = ("[system directive:", "<!-- omo_internal_")

_PROVENANCE = ("user_text", "assistant_text", "tool_result", "system",
               "injected_system", "unknown")


def derive_provenance(role: str, text: str) -> str:
    """Classify a normalized message into a provenance bucket using only
    structured role info plus structural envelope markers. Never prose."""
    stripped = (text or "").lstrip()
    folded_open = _fold(stripped[:40])
    if role == "user" and folded_open.startswith(_INJECTED_OPENERS):
        return "injected_system"
    lowered = stripped[:40].lower()
    if role == "user":
        return "user_text"
    if role == "tool":
        return "tool_result"
    if role == "system":
        return "system"
    if role == "assistant":
        if lowered.startswith(_TOOL_ENVELOPES):
            return "tool_result"
        if lowered.startswith(_SYSTEM_ENVELOPES):
            return "system"
        return "assistant_text"
    return "unknown"


# Signal eligibility by provenance. Rationale:
# - correction/rediscovery/manual describe USER friction: only user-authored
#   text counts (a plan file echoing "manualmente" is not user friction).
# - retry phrases count only when the user reports them; tool-side "retry"
#   vocabulary is handled by session-level recurrence in analyze.py.
# - success counts user + assistant prose (assistant "shipped" is weak but
#   harmless; confidence separates them).
# - error counts user + assistant prose + tool diagnostics; provenance is
#   recorded per signal so confidence can weight corroboration.
_ELIGIBLE: dict[str, set[str]] = {
    "user_correction": {"user_text"},
    "rediscovery": {"user_text"},
    "manual_procedure": {"user_text"},
    "success_pattern": {"user_text", "assistant_text"},
    "error_mention": {"user_text", "assistant_text", "tool_result"},
    "retry_loop": {"user_text"},
}


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
            r"that is not what i (asked|meant)",
            r"wrong (file|approach|direction)",
        ],
        "es": [
            r"eso no es (lo que|asi|correcto)",
            r"no(,|\s)+(eso\s+)?no es (lo que|asi|correcto)",
            r"te (equivocas|equivocaste|confundiste)",
            r"estas mal",
            r"no(,|\s)+(era|queria|me referia|necesito)",
            r"no era eso",
            r"no quiero (eso|esto|lo|lo otro|asi)\b",
            r"me referia a",
            r"me habias dicho",
            r"lo que (quiero|pedi|te pedi|necesito)",
            r"no te pedi",
            r"eso no te (pedi|pregunte)",
            r"por que (interrumpiste|paraste|pausaste|cortaste)",
            # Prohibition + todavia needs an imperative verb: "no pases
            # todavia" and "no hagas ningun cambio todavia" are redirects;
            # "quiero que trabajes ... todavia" or "todavia no termine"
            # are ordinary intent/status, not corrections.
            r"no\s+(pases|pasa|sigas|sigue|sigan|hagas|haga|hagan|avances|"
            r"avancen|implementes|toques|toquen|cambies|subas|borres|"
            r"ejecutes|continues|frenes|pares)\w*\b.{0,30}\btodavia\b",
            r"\btodavia\s+no\s+(hagas|haga|implementes|avances|toques)\w*\b",
            r"\bfrena\b",
            r"recorda( que|te que)",
            r"acordate que",
            r"vos (me habias dicho|dijiste|antes me habias|antes dijiste)",
        ],
    },
    "error_mention": {
        "user_only": False,
        # Broad patterns apply to assistant prose and tool diagnostics.
        "en": [
            r"error|failed|failure|traceback|exception|panic|timeout|timed out|"
            r"not found|denied|refused|broken|crash(?:ed|es)?",
        ],
        "es": [
            r"fallo|falla|fallen|fallaron|fallido|excepcion|roto|se rompio|"
            r"denegado|no encontrado|colgo|cuelga|tildo|no responde",
        ],
        # Strict occurrence structures for user-authored text: task
        # descriptions merely mentioning errors ("auditing for FACTUAL
        # ERRORS") must not fire, while real reports do.
        "user_en": [
            r"i (got|get|have|see|saw|hit|ran into) (an? )?error",
            r"there'?s (an? )?error",
            r"failed (with|to|on)",
            r"keeps? failing",
            r"crash(?:ed|es|ing)?",
            r"\b(broke|broken)\b",
            r"doesn'?t work|not working|won'?t (start|run|work|load|open)",
            r"threw (an? )?(error|exception)",
            r"error (occurred|when|while|:)",
            r"build (failed|broken|failing)",
            r"tests? fail(?:ed|ing|ure)?",
        ],
        "user_es": [
            r"me dio (un )?error",
            r"dio error",
            r"hay un error",
            r"tengo un error",
            r"\bfallo\b|\bfalla(n)?\b|fallando|fallido",
            r"se rompio|esta roto|rota",
            r"no funciona|no anda|no abre|no responde|no carga",
            r"error (al|de|en|:)",
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
# Strict user-authored occurrence patterns (only for signals that define
# user_en/user_es). Leading \b keeps the matching contract ("terror" != error).
_USER_PATTERNS: dict[str, list] = {
    name: [re.compile(r"\b(?:" + p + r")", re.I) for p in
           defs.get("user_en", []) + defs.get("user_es", [])]  # type: ignore[operator]
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


MAX_CHARS_PER_EVENT = 4000


def _text_from_value(value: object) -> str:
    """Extract human text from a content-shaped value. Strings pass through;
    lists join their text parts (e.g. [{"type": "text", "text": "..."}]);
    dicts try `text`, then Copilot Chronicle `content` / `detailedContent`
    (verified live: tool.execution_complete carries
    data.result={content, detailedContent}); anything else yields "" rather
    than a JSON dump of an opaque envelope."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_text_from_value(item) for item in value]
        return "\n".join(p for p in parts if p.strip())
    if isinstance(value, dict):
        for key in ("text", "content", "detailedContent"):
            text = value.get(key, "")
            if isinstance(text, str) and text.strip():
                return text
        return ""
    return ""


def extract_text(msg: dict) -> str:
    """Extract message text from top-level or enveloped content.

    Order: explicit top-level `content` (existing vendors), then Copilot
    Chronicle `data.content`, then `payload.content` (tolerance matching
    upstream envelope handling). Tool payloads additionally try
    result/output/summary keys. Returns "" when nothing interpretable
    exists; callers distinguish that from real content for coverage.
    Never stringifies whole event envelopes.
    """
    if not isinstance(msg, dict):
        return ""
    top = msg.get("content", "")
    if isinstance(top, str) and top.strip():
        return top[:MAX_CHARS_PER_EVENT]
    if isinstance(top, (dict, list)):
        text = _text_from_value(top)
        if text.strip():
            return text[:MAX_CHARS_PER_EVENT]
        if top:
            # Legacy fallback: top-level structured content with no
            # recognized text shape is dumped rather than dropped, exactly
            # as previous versions behaved for existing vendors.
            return json.dumps(top)[:MAX_CHARS_PER_EVENT]
        return ""
    for envelope in ("data", "payload", "metadata"):
        env = msg.get(envelope, "")
        if isinstance(env, dict):
            text = _text_from_value(env.get("content", ""))
            if text.strip():
                return text[:MAX_CHARS_PER_EVENT]
            for key in ("result", "output", "summary", "message"):
                text = _text_from_value(env.get(key, ""))
                if text.strip():
                    return text[:MAX_CHARS_PER_EVENT]
    return ""


def extract_signals(role: str, text: str, ref: str, prov: str | None = None) -> list[dict]:
    """Heuristic per-message signal detection. All matches are DERIVED.

    prov gates eligibility; when omitted it is derived (kept for
    backward-compatible direct callers in tests).
    """
    is_user = (role == "user")
    if prov is None:
        prov = "user_text" if is_user else "assistant_text"
    folded = _fold(text or "")
    raw = text or ""
    found = []
    for stype in _COMPILED:
        if stype in _USER_ONLY and not is_user:
            continue
        if prov not in _ELIGIBLE.get(stype, set()):
            continue
        patterns = _COMPILED[stype]
        if prov == "user_text" and _USER_PATTERNS.get(stype):
            patterns = _USER_PATTERNS[stype]
        if any(p.search(folded) for p in patterns):
            found.append({"type": stype, "excerpt": _excerpt(raw[:400]),
                          "ref": ref, "prov": prov})
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
        "timestamps": "none", "out_of_period": False,
        "raw_event_count": 0, "interpretable_messages": 0,
        "unknown_role_count": 0, "missing_content_count": 0,
        "unsupported_event_count": 0, "content_coverage": "empty"},
    }
    try:
        raw, recovered = export_session(
            session_id, entry.get("source_id", "local"), timeout_s=timeout_s)
        session["metadata"]["recovered_from_panic"] = recovered
    except CassError:
        session["metadata"]["export_failed"] = True
        return session
    session["metadata"]["raw_event_count"] = len(raw)
    raw = [m for m in raw if isinstance(m, dict)]
    total = len(raw)
    session["metadata"]["total_messages"] = total
    unsupported = 0
    malformed = session["metadata"]["raw_event_count"] - total
    if malformed > 0:
        # Non-object records in the export: uninterpretable by construction.
        unsupported += malformed
    bound_start = entry.get("_period_start")
    bound_end = entry.get("_period_end")
    # Full scan for signals FIRST so late-session behavior is discoverable;
    # retention below is only about which message bodies are kept.
    scanned: list[tuple[int, str, str, str, str | None]] = []  # (index, role, prov, text, time)
    user_idx: list[int] = []
    interpretable = 0
    unknown_roles = 0
    missing_content = 0
    for i, msg in enumerate(raw):
        role = infer_role(msg)
        text = extract_text(msg)
        prov = derive_provenance(role, text)
        scanned.append((i, role, prov, text, message_time(msg)))
        if role == "user":
            user_idx.append(i)
        if prov in ("user_text", "assistant_text", "tool_result") and text.strip():
            interpretable += 1
        elif prov == "unknown":
            # Recognized event shape unknown to us, or no role signal at
            # all: counted honestly instead of silently dropped.
            unknown_roles += 1
            unsupported += 1
        elif prov in ("user_text", "assistant_text") and not text.strip():
            # A conversational turn with no decodable text.
            missing_content += 1
            unsupported += 1
    session["metadata"]["interpretable_messages"] = interpretable
    session["metadata"]["unknown_role_count"] = unknown_roles
    session["metadata"]["missing_content_count"] = missing_content
    session["metadata"]["unsupported_event_count"] = unsupported
    if total == 0 or (interpretable == 0 and unknown_roles == 0 and missing_content == 0):
        # No records at all, or only system/injected/control records:
        # nothing to interpret, but nothing lost either.
        session["metadata"]["content_coverage"] = "empty"
    elif interpretable == 0:
        session["metadata"]["content_coverage"] = "none"
    elif unsupported > 0:
        session["metadata"]["content_coverage"] = "partial"
    else:
        session["metadata"]["content_coverage"] = "full"
    ordered_signals: list[dict] = []
    signal_idx: list[int] = []
    for i, role, prov, text, msg_time in scanned:
        found = extract_signals(role, text, f"{session_id}#msg{i}", prov)
        for signal in found:
            signal["period"] = _signal_period(msg_time, bound_start, bound_end)
        if found:
            ordered_signals.append((i, found))
            signal_idx.append(i)
    session["signals"] = [s for _, group in ordered_signals for s in group]
    # Activity range from message timestamps, else inventory fields.
    stamps = []
    for i, _, _, _, msg_time in scanned:
        if msg_time:
            stamps.append(msg_time)
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
    by_index = {i: (role, text) for i, role, _, text, _ in scanned}
    for i in keep:
        role, text = by_index[i]
        session["messages"].append({"role": role, "excerpt": _excerpt(text)})
        tools = raw[i].get("tool_calls") or raw[i].get("toolCalls") or []
        if isinstance(tools, list):
            for call in tools[:20]:
                name = call.get("name") if isinstance(call, dict) else str(call)
                session["tool_calls"].append(str(name)[:120])
        data = raw[i].get("data", "")
        if isinstance(data, dict):
            for call in data.get("toolRequests", [])[:20]:
                if isinstance(call, dict):
                    for key in ("tool", "name", "toolName"):
                        if isinstance(call.get(key), str) and call[key].strip():
                            session["tool_calls"].append(call[key].strip()[:120])
                            break
    return session


def _signal_period(msg_time: str | None, bound_start: str | None,
                   bound_end: str | None) -> str:
    """Period provenance of one signal from its source message timestamp.

    in_window | pre_window | post_window | unknown (message time missing
    while bounds exist) | unbounded (no requested bounds at all: legacy
    inputs count toward promotion, honestly labeled).
    """
    if msg_time is None:
        return "unknown" if (bound_start or bound_end) else "unbounded"
    if bound_start and msg_time < bound_start:
        return "pre_window"
    if bound_end and msg_time > bound_end:
        return "post_window"
    return "in_window"


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
    content_none = sum(1 for s in ok
                       if s["metadata"].get("content_coverage") == "none")
    content_partial = sum(1 for s in ok
                          if s["metadata"].get("content_coverage") == "partial")
    by_type: dict[str, int] = {}
    by_prov: dict[str, int] = {}
    for s in ok:
        for sg in s["signals"]:
            by_type[sg["type"]] = by_type.get(sg["type"], 0) + 1
            by_prov[sg.get("prov", "unknown")] = by_prov.get(sg.get("prov", "unknown"), 0) + 1
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
            "sessions_content_none": content_none,
            "sessions_content_partial": content_partial,
            "signals_by_type": dict(sorted(by_type.items())),
            "signals_by_provenance": dict(sorted(by_prov.items())),
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
