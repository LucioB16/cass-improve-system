#!/usr/bin/env python3
"""Canonical skill installer (shared logic for all platforms).

Model:
    <home>/.agents/skills/cass-improve-system/   = canonical physical skill
    <home>/.claude/skills/cass-improve-system    = compatibility link (junction
                                                  on Windows, symlink on POSIX)
    <home>/.cline/skills/cass-improve-system     = compatibility link (junction
                                                  on Windows, symlink on POSIX)

Thin per-OS launchers (install-skill.ps1 / install-skill.sh) delegate here
so behavior cannot diverge between platforms.

Safety rules:
- Every compatibility path is a link (or resolves through one), never a
  second copy.
- An existing correct link is accepted (idempotent reinstall).
- A path that resolves to canonical through a parent-level link
  (resolved-alias) is accepted and never modified or removed by this tool:
  only its owner may change it.
- A broken link is repaired.
- A link pointing elsewhere, or a physically divergent tree, FAILS
  safely instead of being overwritten blindly.
- A physically identical, genuinely separate tree is migrated to a link
  (byte-identity proven first via manifest), except in uninstall paths.
- Uninstalling a compatibility link never touches the canonical directory.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys

SKILL_DIRNAME = "cass-improve-system"

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


class InstallError(Exception):
    pass


def default_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "skill", SKILL_DIRNAME))


def canonical_dir(home: str) -> str:
    return os.path.join(home, ".agents", "skills", SKILL_DIRNAME)


CONSUMERS = ("claude", "cline")
CONSUMER_ROOTS = {"claude": ".claude", "cline": ".cline"}


def consumer_dir(home: str, consumer: str) -> str:
    if consumer not in CONSUMERS:
        raise InstallError(f"unknown consumer: {consumer}")
    return os.path.join(home, CONSUMER_ROOTS[consumer], "skills", SKILL_DIRNAME)


def claude_dir(home: str) -> str:
    return consumer_dir(home, "claude")


def cline_dir(home: str) -> str:
    return consumer_dir(home, "cline")


def _is_windows_junction(path: str) -> bool:
    if os.name != "nt":
        return False
    try:
        func = ctypes.windll.kernel32.GetFileAttributesW
        func.restype = ctypes.c_uint32  # DWORD; default c_int turns -1 into truthy
        func.argtypes = [ctypes.c_wchar_p]
        attrs = func(str(path))
    except (AttributeError, OSError):
        return False
    return attrs != 0xFFFFFFFF and bool(attrs & 0x400)


def is_link(path: str) -> bool:
    return os.path.islink(path) or _is_windows_junction(path)


def _same_target(link: str, canonical: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(link)) == os.path.normcase(
            os.path.realpath(canonical))
    except OSError:
        return False


def manifest_tree(root: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name.endswith(EXCLUDE_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                entries[rel] = hashlib.sha256(fh.read()).hexdigest()
    return entries


def manifest_diff(a: dict[str, str], b: dict[str, str]) -> list[str]:
    problems = []
    for rel in sorted(set(a) | set(b)):
        if rel not in a:
            problems.append(f"EXTRA installed-only: {rel}")
        elif rel not in b:
            problems.append(f"MISSING from installed: {rel}")
        elif a[rel] != b[rel]:
            problems.append(f"STALE content: {rel}")
    return problems


def create_link(link: str, canonical: str) -> None:
    parent = os.path.dirname(link)
    os.makedirs(parent, exist_ok=True)
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", link, canonical],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise InstallError(f"mklink /J failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    else:
        os.symlink(canonical, link)


def remove_link_only(link: str) -> None:
    """Remove a link itself, never its target.

    os.unlink removes symlinks on every platform; os.rmdir removes Windows
    junctions (os.rmdir on a POSIX symlink-to-directory raises
    NotADirectoryError, so it must not be used there).
    """
    if os.path.islink(link):
        os.unlink(link)
        return
    if _is_windows_junction(link):
        os.rmdir(link)
        return
    raise InstallError(f"refusing to remove non-link: {link}")


def _is_same_tree(path: str, canonical: str) -> bool:
    """True when path and canonical are the same filesystem object (e.g. the
    path resolves through a parent-level junction/symlink). Read-only."""
    try:
        return os.path.samefile(path, canonical)
    except OSError:
        return False


def classify_link_path(link: str, canonical: str, consumer: str = "claude") -> tuple[str, str]:
    """Classify a compatibility path. States: missing | correct-link |
    resolved-alias | broken-link | wrong-link | physical-identical |
    physical-divergent | blocking-file.

    resolved-alias = not itself a link, but resolves to canonical (typically
    a parent directory is the actual link). Accepted everywhere a correct
    link is, and never modified or removed by this tool.
    """
    label = f"{consumer}-link"
    if os.path.lexists(link):
        if is_link(link):
            if not os.path.exists(link):
                return ("broken-link", "points to a missing target")
            if _same_target(link, canonical):
                return ("correct-link", f"resolves to {os.path.realpath(link)}")
            return ("wrong-link", f"resolves to {os.path.realpath(link)}")
        if os.path.isdir(link):
            if _same_target(link, canonical):
                return ("resolved-alias",
                        f"resolves to canonical through a parent link ({label} untouched)")
            src_manifest = manifest_tree(canonical) if os.path.isdir(canonical) else {}
            diff = manifest_diff(manifest_tree(link), src_manifest)
            if not diff and src_manifest:
                return ("physical-identical", "byte-identical physical copy")
            detail = "; ".join(diff[:5])
            return ("physical-divergent", detail or "empty or unreadable copy")
        return ("blocking-file", "exists and is not a directory")
    return ("missing", "nothing installed")


def classify_claude_path(link: str, canonical: str) -> tuple[str, str]:
    return classify_link_path(link, canonical, "claude")


def install_canonical(source: str, home: str, force: bool = False) -> str:
    dest = canonical_dir(home)
    if not os.path.isdir(source):
        raise InstallError(f"source skill tree not found: {source}")
    if os.path.lexists(dest) and not force:
        raise InstallError(
            f"canonical installation already exists: {dest} (use --force to overwrite)")
    if os.path.lexists(dest):
        if is_link(dest):
            remove_link_only(dest)
        else:
            shutil.rmtree(dest, ignore_errors=False)
    shutil.copytree(
        source, dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    return dest


def ensure_consumer_link(home: str, consumer: str) -> tuple[str, str]:
    """Ensure a compatibility link. Returns (action, detail) with action in
    created|accepted|repaired|migrated."""
    link = consumer_dir(home, consumer)
    canonical = canonical_dir(home)
    if not os.path.isdir(canonical) or is_link(canonical):
        raise InstallError(f"canonical installation missing: {canonical}")
    state, detail = classify_link_path(link, canonical, consumer)
    if state in ("correct-link", "resolved-alias"):
        return ("accepted", detail)
    if state == "missing":
        create_link(link, canonical)
        return ("created", f"link -> {canonical}")
    if state == "broken-link":
        remove_link_only(link)
        create_link(link, canonical)
        return ("repaired", f"was broken ({detail}); recreated")
    if state == "physical-identical":
        if _is_same_tree(link, canonical):
            raise InstallError(
                f"refusing to migrate {consumer} path that aliases canonical: {link}")
        shutil.rmtree(link, ignore_errors=False)
        create_link(link, canonical)
        return ("migrated", "identical physical copy replaced by link")
    raise InstallError(
        f"refusing to touch {consumer} path ({state}: {detail}); "
        "back it up or remove it manually, then re-run")


def ensure_claude_link(home: str) -> tuple[str, str]:
    return ensure_consumer_link(home, "claude")


def verify_installation(source: str, home: str) -> tuple[list[str], list[str]]:
    """Return (problems, notes). Empty problems = verified."""
    problems: list[str] = []
    notes: list[str] = []
    canonical = canonical_dir(home)
    if not os.path.isdir(canonical) or is_link(canonical):
        return ([f"MISSING canonical installation: {canonical}"], notes)
    problems.extend(manifest_diff(manifest_tree(canonical), manifest_tree(source)))
    for consumer in CONSUMERS:
        link = consumer_dir(home, consumer)
        state, detail = classify_link_path(link, canonical, consumer)
        if state == "missing":
            notes.append(f"no {consumer} compatibility link (canonical copy is complete)")
        elif state in ("correct-link", "resolved-alias"):
            notes.append(f"{consumer}: VERIFIED -> canonical ({detail})")
        else:
            problems.append(f"{state} {consumer}-link: {detail}")
    return (problems, notes)


LINK_TARGETS = ("claude", "cline")


def uninstall(home: str, target: str, force: bool = False) -> str:
    if target in LINK_TARGETS:
        link = consumer_dir(home, target)
        if not os.path.lexists(link):
            return f"{target}-link: nothing installed"
        if not is_link(link):
            raise InstallError(
                f"refusing to remove non-link {target} path without review: {link} "
                "(it may alias canonical through a parent link; only its owner "
                "may change it)")
        remove_link_only(link)
        return f"{target}-link: removed (canonical untouched)"
    if target == "agents":
        dest = canonical_dir(home)
        if not os.path.lexists(dest):
            return "canonical: nothing installed"
        if is_link(dest):
            raise InstallError(f"canonical path is a link, refusing: {dest}")
        if not force:
            raise InstallError(
                f"refusing to delete canonical installation without --force: {dest}")
        shutil.rmtree(dest, ignore_errors=False)
        return "canonical: removed"
    raise InstallError(f"unknown uninstall target: {target}")


def _install_plan(target: str) -> tuple[bool, list[str]]:
    """Return (install_canonical, consumers). both keeps its historical
    meaning (agents + claude); all adds cline."""
    if target == "agents":
        return (True, [])
    if target == "claude":
        return (False, ["claude"])
    if target == "cline":
        return (False, ["cline"])
    if target == "both":
        return (True, ["claude"])
    if target == "all":
        return (True, ["claude", "cline"])
    raise InstallError(f"unknown install target: {target}")


def _uninstall_order(target: str) -> list[str]:
    if target == "both":
        return ["claude", "agents"]  # historical meaning preserved
    if target == "all":
        return ["claude", "cline", "agents"]  # links first, canonical last
    return [target]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical skill installer.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", default=None)
    common.add_argument("--home", default=None)
    common.add_argument("--force", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p_install = sub.add_parser("install", parents=[common])
    p_install.add_argument("--target", default="both",
                           choices=["agents", "both", "claude", "cline", "all"])
    sub.add_parser("verify", parents=[common])
    p_uninstall = sub.add_parser("uninstall", parents=[common])
    p_uninstall.add_argument("--target", default="claude",
                             choices=["agents", "both", "claude", "cline", "all"])
    args = parser.parse_args(argv)
    home = os.path.abspath(os.path.expanduser(args.home or "~"))
    source = os.path.abspath(args.source or default_source())
    try:
        if args.command == "install":
            want_canonical, want_links = _install_plan(args.target)
            if want_canonical:
                dest = install_canonical(source, home, force=args.force)
                print(f"canonical: {dest}")
            for consumer in want_links:
                action, detail = ensure_consumer_link(home, consumer)
                print(f"{consumer}-link: {action} ({detail})")
        elif args.command == "verify":
            problems, notes = verify_installation(source, home)
            print("canonical:")
            for note in notes:
                print(f"  note: {note}")
            if problems:
                print("VERIFICATION FAILED:")
                for problem in problems:
                    print(f"  {problem}")
                return 2
            print(f"verified: {canonical_dir(home)} byte-identical to source")
        elif args.command == "uninstall":
            for step in _uninstall_order(args.target):
                print(uninstall(home, step, force=args.force))
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
