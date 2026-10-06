"""Period expressions for reviews.

Supported inputs (case-insensitive, all mapped to CASS --since/--until syntax):
  "last 24h" / "last 24 hours"      -> since "-24h"
  "last N days" / "last Nd" / "N days" -> since "-Nd"
  "today" / "yesterday" / "last week" -> CASS keywords
  "2026-10-01..2026-10-06" / "2026-10-01 through 2026-10-06" / "from X to Y"
                                    -> explicit since/until (ISO dates)
  explicit --since/--until passthrough for anything CASS already accepts
"""

from __future__ import annotations

import re

_EXPLICIT = [
    re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:\.\.|through|to|-|—)\s*(\d{4}-\d{2}-\d{2})\s*$", re.I),
    re.compile(r"^\s*from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s*$", re.I),
]
_LAST_N_DAYS = re.compile(r"^\s*last\s+(\d+)\s*(d|day|days)\s*$", re.I)
_N_DAYS = re.compile(r"^\s*(\d+)\s*(d|day|days)\s*$", re.I)
_LAST_24H = re.compile(r"^\s*last\s+24\s*(h|hour|hours)\s*$", re.I)
_KEYWORDS = {"today", "yesterday", "week", "last week"}


def parse_period(text: str) -> tuple[str | None, str | None, str]:
    """Return (since, until, label). Raises ValueError on unparseable input."""
    cleaned = " ".join(text.strip().split())
    if _LAST_24H.match(cleaned) or cleaned.lower() in ("last 24h", "last day"):
        return "-24h", None, "last 24 hours"
    m = _LAST_N_DAYS.match(cleaned) or _N_DAYS.match(cleaned)
    if m:
        days = int(m.group(1))
        if days < 1 or days > 365:
            raise ValueError(f"day count out of range (1-365): {days}")
        return f"-{days}d", None, f"last {days} day{'s' if days != 1 else ''}"
    if cleaned.lower() in _KEYWORDS:
        return cleaned.lower(), None, cleaned.lower()
    for pattern in _EXPLICIT:
        m = pattern.match(cleaned)
        if m:
            start, end = m.group(1), m.group(2)
            if start > end:
                raise ValueError(f"start {start} is after end {end}")
            return start, end, f"{start} through {end}"
    raise ValueError(
        f"Could not parse period {text!r}. Use 'last 24h', 'last N days', "
        "'today', or 'YYYY-MM-DD..YYYY-MM-DD'."
    )
