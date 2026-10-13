#!/usr/bin/env python3
"""Repository-hygiene guard: no machine-specific paths in tracked files.

Public documentation must stay portable. A concrete user-home path
(`C:\\Users\\somebody\\...`, `/home/somebody/...`) publishes one machine's
local identity and teaches readers to copy a path that does not exist on
their host. Portable forms are fine and encouraged:

    %USERPROFILE%\\...      $HOME/...      ~/...
    C:\\Users\\<USERNAME>\\...   C:\\Users\\USERNAME\\...

Canonical project URLs (`https://github.com/LucioB16/cass-improve-system`)
are legitimate project identity and are never flagged.

The scanner is a pure function over text, so the pattern tests below never
write a forbidden string to disk. The repository scan skips this module
itself (documented below) so its own synthetic fixtures cannot self-match.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# This module holds synthetic forbidden paths for its own tests, so it is
# excluded from the repository-wide scan. Nothing else is excluded.
SELF_EXCLUDED = {"tests/test_repo_hygiene.py"}

# A home-directory segment that is a placeholder, not a real username.
PLACEHOLDER_NAMES = {
    "USERNAME", "USER", "NAME", "YOU", "YOURNAME", "YOURUSER",
    "SOMEONE", "SOMEBODY", "EXAMPLE", "TESTUSER", "ME", "MYUSER",
    "HOME", "USERPROFILE",
}

# Windows: C:\Users\<name>\ or C:/Users/<name>/
WINDOWS_HOME = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}[Uu]sers[\\/]{1,2}"
    r"(<[A-Za-z0-9._-]+>|[A-Za-z0-9._-]+)")

# POSIX: /home/<name>/ or /Users/<name>/ (the latter is macOS)
POSIX_HOME = re.compile(
    r"(?<![A-Za-z0-9.:])/(?:home|Users)/"
    r"(<[A-Za-z0-9._-]+>|[A-Za-z0-9._-]+)(?![A-Za-z0-9._-])")

# Known local project roots of the machine this repository was authored on.
# Kept as a deny-list so they cannot silently reappear in documentation.
LOCAL_ROOTS = ("D:\\Proyectos", "D:/Proyectos")


def _is_placeholder(segment: str) -> bool:
    return segment.strip("<>").upper() in PLACEHOLDER_NAMES


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return [(line_number, matched_text, rule)] violations for `text`."""
    violations: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in (WINDOWS_HOME, POSIX_HOME):
            for match in pattern.finditer(line):
                segment = match.group(1)
                if _is_placeholder(segment):
                    continue
                violations.append(
                    (number, match.group(0), "concrete user-home path"))
        lowered = line.lower()
        for root in LOCAL_ROOTS:
            if root.lower() in lowered:
                violations.append((number, root, "known local project root"))
    return violations


def tracked_text_files() -> list[str]:
    """Tracked, decodable-as-text repository files (never .git or artifacts)."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("git ls-files failed; hygiene scan cannot run")
    paths = [p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]
    text_files = []
    for rel in sorted(paths):
        if rel in SELF_EXCLUDED:
            continue
        full = os.path.join(REPO_ROOT, rel)
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
            raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary asset or unreadable: not documentation
        text_files.append(rel)
    return text_files


class PortableFormTest(unittest.TestCase):
    """Forms that MUST stay allowed (they publish no local identity)."""

    ALLOWED = [
        "%USERPROFILE%\\.agents\\skills",
        "C:\\Users\\<USERNAME>\\.cline\\skills",
        "C:\\Users\\USERNAME\\.claude\\skills",
        "$HOME/.agents/skills",
        "~/project",
        "<home>/.agents/skills/cass-improve-system/",
        "https://github.com/LucioB16/cass-improve-system",
        "export from C:\\Users\\<USERNAME>\\AppData\\Local\\Temp",
    ]

    def test_portable_forms_are_accepted(self):
        for text in self.ALLOWED:
            with self.subTest(text=text):
                self.assertEqual(scan_text(text), [])

    def test_canonical_repo_url_is_never_flagged(self):
        url = "https://github.com/LucioB16/cass-improve-system"
        self.assertEqual(scan_text(url), [])
        self.assertEqual(scan_text(f"see {url} for install steps"), [])


class MachineSpecificPathTest(unittest.TestCase):
    """Forms that MUST be rejected (they name a concrete local identity)."""

    REJECTED = [
        "C:\\Users\\analyst1\\.agents\\skills",
        "C:/Users/analyst1/.agents/skills",
        "path is C:\\Users\\analyst1\\AppData\\Local\\Temp",
        "/home/analyst1/.agents/skills",
        "/Users/analyst1/.agents/skills",
        "D:\\Proyectos\\cass-improve-system",
    ]

    def test_concrete_home_paths_are_rejected(self):
        for text in self.REJECTED:
            with self.subTest(text=text):
                found = scan_text(text)
                self.assertTrue(found, f"expected a violation for {text!r}")

    def test_angle_brackets_alone_are_not_a_free_pass(self):
        # Only known placeholder tokens are exempt; a real name does not
        # become publishable by wrapping it in angle brackets.
        self.assertEqual(scan_text("C:\\Users\\<USERNAME>\\x"), [])
        self.assertTrue(scan_text("C:\\Users\\<analyst1>\\x"))
        self.assertTrue(scan_text("/home/<analyst1>/x"))


class RepositoryScanTest(unittest.TestCase):
    """The actual tracked tree must be clean."""

    def test_tracked_tree_has_no_machine_specific_paths(self):
        violations = []
        for rel in tracked_text_files():
            full = os.path.join(REPO_ROOT, rel)
            with open(full, encoding="utf-8") as fh:
                for number, matched, rule in scan_text(fh.read()):
                    violations.append(f"{rel}:{number}: {rule}: {matched}")
        self.assertEqual(
            violations, [],
            "machine-specific paths found in tracked files:\n  "
            + "\n  ".join(violations))

    def test_scan_covers_tracked_documentation(self):
        files = tracked_text_files()
        for expected in ("README.md", "PLAN.md", "docs/testing.md",
                         "docs/cass-integration.md", "AGENTS.md"):
            self.assertIn(expected, files)
        self.assertNotIn("tests/test_repo_hygiene.py", files)


if __name__ == "__main__":
    unittest.main()
