"""Deterministic tests for the canonical install/link model. Stdlib only.

All tests run against isolated temporary HOME directories (including one
with a space in the path), never the real user profile. No CASS required.
"""
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
REPO_SCRIPTS = os.path.abspath(os.path.join(BASE, "..", "scripts"))
sys.path.insert(0, REPO_SCRIPTS)

import skill_install


def make_source(root):
    src = os.path.join(root, "src")
    os.makedirs(os.path.join(src, "scripts"))
    with open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("skill")
    with open(os.path.join(src, "scripts", "run.py"), "w", encoding="utf-8") as fh:
        fh.write("print(1)")
    return src


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fake home ")
        self.home = self.tmp.name
        self.source = make_source(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_canonical_layout(self):
        dest = skill_install.install_canonical(self.source, self.home)
        self.assertEqual(dest, skill_install.canonical_dir(self.home))
        self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "scripts", "run.py")))

    def test_reinstall_requires_force(self):
        skill_install.install_canonical(self.source, self.home)
        with self.assertRaises(skill_install.InstallError):
            skill_install.install_canonical(self.source, self.home)
        skill_install.install_canonical(self.source, self.home, force=True)

    def test_cli_accepts_options_after_subcommand(self):
        code = skill_install.main([
            "install", "--target", "both", "--home", self.home,
            "--source", self.source, "--force"])
        self.assertEqual(code, 0)
        code = skill_install.main(["verify", "--home", self.home,
                                   "--source", self.source])
        self.assertEqual(code, 0)
        self.assertTrue(skill_install.is_link(
            skill_install.claude_dir(self.home)))

    def test_bytecode_never_deployed(self):
        cache = os.path.join(self.source, "scripts", "__pycache__")
        os.makedirs(cache)
        with open(os.path.join(cache, "run.pyc"), "w") as fh:
            fh.write("x")
        dest = skill_install.install_canonical(self.source, self.home)
        self.assertFalse(os.path.exists(os.path.join(dest, "scripts", "__pycache__")))

    def test_verify_clean(self):
        skill_install.install_canonical(self.source, self.home)
        problems, _ = skill_install.verify_installation(self.source, self.home)
        self.assertEqual(problems, [])

    def test_verify_detects_stale(self):
        dest = skill_install.install_canonical(self.source, self.home)
        with open(os.path.join(dest, "SKILL.md"), "w") as fh:
            fh.write("drifted")
        problems, _ = skill_install.verify_installation(self.source, self.home)
        self.assertTrue(any("STALE" in p for p in problems))

    def test_verify_detects_missing_and_extra(self):
        dest = skill_install.install_canonical(self.source, self.home)
        os.remove(os.path.join(dest, "SKILL.md"))
        with open(os.path.join(dest, "rogue.txt"), "w") as fh:
            fh.write("r")
        problems, _ = skill_install.verify_installation(self.source, self.home)
        self.assertTrue(any("MISSING" in p for p in problems))
        self.assertTrue(any("EXTRA" in p for p in problems))


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fake home ")
        self.home = self.tmp.name
        self.source = make_source(self.tmp.name)
        skill_install.install_canonical(self.source, self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_accept_link(self):
        action, _ = skill_install.ensure_claude_link(self.home)
        self.assertEqual(action, "created")
        self.assertTrue(skill_install.is_link(skill_install.claude_dir(self.home)))
        action, _ = skill_install.ensure_claude_link(self.home)
        self.assertEqual(action, "accepted")

    def test_broken_link_repaired(self):
        link = skill_install.claude_dir(self.home)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", link,
                            os.path.join(self.home, "nope")],
                           check=True, capture_output=True)
        else:
            os.symlink(os.path.join(self.home, "nope"), link)
        state, _ = skill_install.classify_claude_path(
            link, skill_install.canonical_dir(self.home))
        self.assertEqual(state, "broken-link")
        action, _ = skill_install.ensure_claude_link(self.home)
        self.assertEqual(action, "repaired")
        state, _ = skill_install.classify_claude_path(
            link, skill_install.canonical_dir(self.home))
        self.assertEqual(state, "correct-link")

    def test_wrong_target_fails_safely(self):
        other = os.path.join(self.home, "other")
        os.makedirs(other)
        link = skill_install.claude_dir(self.home)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", link, other],
                           check=True, capture_output=True)
        else:
            os.symlink(other, link)
        state, _ = skill_install.classify_claude_path(
            link, skill_install.canonical_dir(self.home))
        self.assertEqual(state, "wrong-link")
        with self.assertRaises(skill_install.InstallError):
            skill_install.ensure_claude_link(self.home)
        self.assertTrue(os.path.isdir(other))  # untouched

    def test_identical_physical_tree_migrates(self):
        import shutil
        link = skill_install.claude_dir(self.home)
        shutil.copytree(skill_install.canonical_dir(self.home), link)
        state, _ = skill_install.classify_claude_path(
            link, skill_install.canonical_dir(self.home))
        self.assertEqual(state, "physical-identical")
        action, _ = skill_install.ensure_claude_link(self.home)
        self.assertEqual(action, "migrated")
        self.assertTrue(skill_install.is_link(link))

    def test_divergent_physical_tree_fails_safely(self):
        import shutil
        link = skill_install.claude_dir(self.home)
        shutil.copytree(skill_install.canonical_dir(self.home), link)
        with open(os.path.join(link, "SKILL.md"), "w") as fh:
            fh.write("user changes")
        state, _ = skill_install.classify_claude_path(
            link, skill_install.canonical_dir(self.home))
        self.assertEqual(state, "physical-divergent")
        with self.assertRaises(skill_install.InstallError):
            skill_install.ensure_claude_link(self.home)
        with open(os.path.join(link, "SKILL.md")) as fh:
            self.assertEqual(fh.read(), "user changes")  # preserved

    def test_link_without_canonical_fails(self):
        import shutil
        shutil.rmtree(skill_install.canonical_dir(self.home))
        with self.assertRaises(skill_install.InstallError):
            skill_install.ensure_claude_link(self.home)


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fake home ")
        self.home = self.tmp.name
        self.source = make_source(self.tmp.name)
        skill_install.install_canonical(self.source, self.home)
        skill_install.ensure_claude_link(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_uninstall_claude_keeps_canonical(self):
        out = skill_install.uninstall(self.home, "claude")
        self.assertIn("canonical untouched", out)
        self.assertFalse(os.path.lexists(skill_install.claude_dir(self.home)))
        self.assertTrue(os.path.isfile(
            os.path.join(skill_install.canonical_dir(self.home), "SKILL.md")))

    def test_uninstall_canonical_requires_force(self):
        with self.assertRaises(skill_install.InstallError):
            skill_install.uninstall(self.home, "agents")
        skill_install.uninstall(self.home, "agents", force=True)
        self.assertFalse(os.path.exists(skill_install.canonical_dir(self.home)))

    def test_uninstall_refuses_physical_claude(self):
        import shutil
        skill_install.uninstall(self.home, "claude")  # remove link first
        shutil.copytree(self.source, skill_install.claude_dir(self.home))
        with self.assertRaises(skill_install.InstallError):
            skill_install.uninstall(self.home, "claude")


class PlatformNeutralRuntimeTest(unittest.TestCase):
    def test_manifest_uses_posix_separators(self):
        import manifest as manifest_mod
        sys.path.insert(0, os.path.join(
            BASE, "..", "skill", "cass-improve-system", "scripts"))
        try:
            entries = manifest_mod.manifest(
                os.path.join(BASE, "..", "skill", "cass-improve-system"))
        finally:
            sys.path.pop()
        self.assertTrue(entries)
        for rel in entries:
            self.assertNotIn("\\", rel)

    def test_temp_dir_is_platform_default(self):
        import tempfile
        self.assertTrue(os.path.isdir(tempfile.gettempdir()))

    def test_home_expansion_portable(self):
        expanded = os.path.expanduser("~")
        self.assertTrue(os.path.isabs(expanded))


if __name__ == "__main__":
    unittest.main()
