"""CASS preflight: graded readiness with actionable guidance.

Exit codes:
  0 - proceed: healthy index, or stale-only index (auto-refreshed or noted)
  2 - CASS missing from PATH
  3 - CASS present but incompatible (version or capabilities)
  4 - index uninitialized or genuinely broken (explicit user action required)

A merely stale index is usable and is never treated as a broken install:
on stale-only state preflight runs one bounded incremental refresh
(`cass index`, never --full) and continues. The refresh writes only CASS's
own derived archive/index state; the review itself stays read-only w.r.t.
project files, instructions, skills, and repositories.
"""

from __future__ import annotations

import argparse
import sys

import cass_adapter
from cass_adapter import CASS_UPSTREAM, MIN_CASS_VERSION, CassError


def preflight(allow_refresh: bool = True) -> tuple[int, str]:
    exe = cass_adapter.find_cass()
    if exe is None:
        return 2, (
            "CASS is required but was not found on PATH. No review was performed.\n"
            f"Install CASS (external project): {CASS_UPSTREAM}\n"
            "Windows: irm https://raw.githubusercontent.com/"
            "Dicklesworthstone/coding_agent_session_search/main/install.ps1 | iex\n"
            "Then re-run this review. See docs/cass-integration.md for details."
        )
    try:
        version = cass_adapter.get_version()
    except CassError as exc:
        return 3, f"CASS at {exe} did not respond to --version. {exc}"
    if version < MIN_CASS_VERSION:
        return 3, (
            f"CASS version {'.'.join(map(str, version))} is older than the minimum "
            f"supported {'.'.join(map(str, MIN_CASS_VERSION))}. "
            "Upgrade CASS, then re-run. No review was performed."
        )
    ok, caps = cass_adapter.check_capabilities()
    if not ok:
        return 3, (
            f"CASS {'.'.join(map(str, version))} lacks required capabilities: {caps}. "
            "Upgrade CASS, then re-run. No review was performed."
        )
    try:
        snapshot = cass_adapter.readiness()
    except CassError as exc:
        return 4, f"CASS health check failed: {exc}"
    state, detail = cass_adapter.classify_readiness(snapshot)
    base = (f"CASS OK: {exe} version {'.'.join(map(str, version))} ({caps})")
    if state == "healthy":
        return 0, base + ", index healthy."
    if state == "stale":
        if not allow_refresh:
            return 4, (
                f"CASS index stale ({detail}). Re-run with refresh allowed, "
                "or run `cass index` (incremental) explicitly and re-run. "
                "No review was performed."
            )
        try:
            refreshed = cass_adapter.refresh_index_incremental()
            new_state, _ = cass_adapter.classify_readiness(cass_adapter.readiness())
            note = (f"stale index auto-refreshed "
                    f"(+{refreshed.get('conversations', '?')} sessions); "
                    f"readiness now: {new_state}")
        except CassError as exc:
            note = (f"stale index refresh failed ({exc}); proceeding anyway — "
                    "the index is usable but may miss the newest sessions")
        return 0, base + f". NOTE: {note}. Include it under Coverage limitations."
    if state == "uninitialized":
        return 4, (
            f"CASS index uninitialized ({detail}). "
            "Run `cass index --full` once to build the archive, then re-run the review. "
            "No review was performed."
        )
    return 4, (
        f"CASS index is genuinely broken ({detail}). No automatic repair was "
        "attempted: inspect `cass status --json` / `cass health --json`, resolve "
        "the reported cause (lock, corruption, disk), then re-run. "
        "No review was performed."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the CASS dependency.")
    parser.add_argument("--no-refresh", action="store_true",
                        help="do not auto-refresh a stale-only index (strict mode)")
    args = parser.parse_args(argv)
    code, message = preflight(allow_refresh=not args.no_refresh)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
