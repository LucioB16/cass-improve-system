#!/usr/bin/env python3
"""Deterministic recursive manifest of a skill tree: {relpath: sha256}.

Bytecode (`__pycache__/`, `*.pyc`, `*.pyo`) is excluded: it is
environment-specific and never part of the installed payload.
Used by scripts/verify-install.ps1 to prove an installed copy is
byte-identical to the repository source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def manifest(root: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name.endswith(EXCLUDE_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                entries[rel] = hashlib.sha256(fh.read()).hexdigest()
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a skill-tree manifest.")
    parser.add_argument("directory")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.directory):
        print(f"manifest: not a directory: {args.directory}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(
        json.dumps(manifest(args.directory), indent=2, sort_keys=True).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
