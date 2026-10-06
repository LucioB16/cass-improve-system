"""CASS preflight: fail fast with actionable guidance when the dependency is unusable.

Exit codes:
  0 - CASS present, compatible, index healthy
  2 - CASS missing from PATH
  3 - CASS present but incompatible (version or capabilities)
  4 - CASS present but index missing/stale (review must not proceed silently)
"""

from __future__ import annotations

import argparse
import sys

import cass_adapter
from cass_adapter import CASS_UPSTREAM, MIN_CASS_VERSION, CassError


def preflight() -> tuple[int, str]:
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
    ok, detail = cass_adapter.check_capabilities()
    if not ok:
        return 3, (
            f"CASS {'.'.join(map(str, version))} lacks required capabilities: {detail}. "
            "Upgrade CASS, then re-run. No review was performed."
        )
    try:
        status = cass_adapter.health()
    except CassError as exc:
        return 4, f"CASS health check failed: {exc}"
    state = status.get("status", "unknown")
    if not status.get("initialized", False):
        return 4, (
            f"CASS index is '{state}' (not initialized). "
            "Run `cass index --full` once to build the archive, then re-run the review. "
            "Indexing mutates CASS state and is intentionally separate from read-only review."
        )
    if status.get("stale", False) or state in ("stale", "maintenance-required"):
        return 4, (
            f"CASS index reports '{state}'. Refresh it explicitly "
            "(`cass index` for incremental) and re-run; the review will not "
            "auto-repair the index."
        )
    return 0, (
        f"CASS OK: {exe} version {'.'.join(map(str, version))} "
        f"({detail}), index healthy."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the CASS dependency.")
    parser.parse_args(argv)
    code, message = preflight()
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
