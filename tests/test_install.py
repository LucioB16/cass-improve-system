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
    CONSUMERS = ("claude", "cline")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fake home ")
        self.home = self.tmp.name
        self.source = make_source(self.tmp.name)
        skill_install.install_canonical(self.source, self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def _link(self, consumer):
        return skill_install.consumer_dir(self.home, consumer)

    def _make_dangling(self, link, target):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           check=True, capture_output=True)
        else:
            os.symlink(target, link)

    def test_create_and_accept_link(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                action, _ = skill_install.ensure_consumer_link(self.home, consumer)
                self.assertEqual(action, "created")
                self.assertTrue(skill_install.is_link(self._link(consumer)))
                action, _ = skill_install.ensure_consumer_link(self.home, consumer)
                self.assertEqual(action, "accepted")

    def test_broken_link_repaired(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                link = self._link(consumer)
                self._make_dangling(link, os.path.join(self.home, "nope"))
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "broken-link")
                action, _ = skill_install.ensure_consumer_link(self.home, consumer)
                self.assertEqual(action, "repaired")
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "correct-link")

    def test_wrong_target_fails_safely(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                other = os.path.join(self.home, f"other-{consumer}")
                os.makedirs(other)
                link = self._link(consumer)
                self._make_dangling(link, other)
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "wrong-link")
                with self.assertRaises(skill_install.InstallError):
                    skill_install.ensure_consumer_link(self.home, consumer)
                self.assertTrue(os.path.isdir(other))  # untouched

    def test_blocking_file_fails_safely(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                link = self._link(consumer)
                os.makedirs(os.path.dirname(link), exist_ok=True)
                with open(link, "w") as fh:
                    fh.write("file in the way")
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "blocking-file")
                with self.assertRaises(skill_install.InstallError):
                    skill_install.ensure_consumer_link(self.home, consumer)

    def test_identical_physical_tree_migrates(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                import shutil
                link = self._link(consumer)
                shutil.copytree(skill_install.canonical_dir(self.home), link)
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "physical-identical")
                action, _ = skill_install.ensure_consumer_link(self.home, consumer)
                self.assertEqual(action, "migrated")
                self.assertTrue(skill_install.is_link(link))

    def test_divergent_physical_tree_fails_safely(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                import shutil
                link = self._link(consumer)
                shutil.copytree(skill_install.canonical_dir(self.home), link)
                with open(os.path.join(link, "SKILL.md"), "w") as fh:
                    fh.write("user changes")
                state, _ = skill_install.classify_link_path(
                    link, skill_install.canonical_dir(self.home), consumer)
                self.assertEqual(state, "physical-divergent")
                with self.assertRaises(skill_install.InstallError):
                    skill_install.ensure_consumer_link(self.home, consumer)
                with open(os.path.join(link, "SKILL.md")) as fh:
                    self.assertEqual(fh.read(), "user changes")  # preserved

    def test_resolved_alias_accepted_never_modified(self):
        # A consumer path that resolves to canonical through a parent-level
        # link (not itself a link) must be accepted and must refuse removal.
        import shutil
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                home_b = os.path.join(self.tmp.name, f"home-{consumer}")
                canonical = skill_install.install_canonical(self.source, home_b)
                link = skill_install.consumer_dir(home_b, consumer)
                parent_link = os.path.dirname(link)
                os.makedirs(os.path.dirname(parent_link), exist_ok=True)
                if os.name == "nt":
                    import subprocess
                    subprocess.run(
                        ["cmd", "/c", "mklink", "/J", parent_link,
                         os.path.dirname(canonical)],
                        check=True, capture_output=True)
                else:
                    os.symlink(os.path.dirname(canonical), parent_link)
                self.assertFalse(skill_install.is_link(link))
                state, _ = skill_install.classify_link_path(
                    link, canonical, consumer)
                self.assertEqual(state, "resolved-alias")
                action, _ = skill_install.ensure_consumer_link(home_b, consumer)
                self.assertEqual(action, "accepted")
                with self.assertRaises(skill_install.InstallError):
                    skill_install.uninstall(home_b, consumer)
                self.assertTrue(os.path.isfile(os.path.join(canonical, "SKILL.md")))

    def test_parent_level_alias_accepted_and_uninstall_refused(self):
        # Faithful replica of a parent-level compatibility junction:
        # <home>/.cline/skills -> <home>/.agents/skills. The consumer path
        # itself is not a link but resolves to canonical.
        import shutil
        home_b = os.path.join(self.tmp.name, "homeB")
        canonical = skill_install.install_canonical(self.source, home_b)
        parent_link = os.path.join(home_b, ".cline", "skills")
        os.makedirs(os.path.dirname(parent_link), exist_ok=True)
        if os.name == "nt":
            import subprocess
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", parent_link,
                 os.path.dirname(canonical)],
                check=True, capture_output=True)
        else:
            os.symlink(os.path.dirname(canonical), parent_link)
        link = skill_install.consumer_dir(home_b, "cline")
        self.assertFalse(skill_install.is_link(link))
        state, _ = skill_install.classify_link_path(link, canonical, "cline")
        self.assertEqual(state, "resolved-alias")
        action, _ = skill_install.ensure_consumer_link(home_b, "cline")
        self.assertEqual(action, "accepted")
        problems, _ = skill_install.verify_installation(self.source, home_b)
        self.assertEqual(problems, [])
        with self.assertRaises(skill_install.InstallError):
            skill_install.uninstall(home_b, "cline")
        self.assertTrue(os.path.isfile(os.path.join(canonical, "SKILL.md")))

    def test_is_link_false_for_missing_path(self):
        # Regression: DWORD sign handling must not report ghosts as links.
        self.assertFalse(skill_install.is_link(
            os.path.join(self.home, "does", "not", "exist")))

    def test_link_without_canonical_fails(self):
        import shutil
        shutil.rmtree(skill_install.canonical_dir(self.home))
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                with self.assertRaises(skill_install.InstallError):
                    skill_install.ensure_consumer_link(self.home, consumer)

    def test_all_installs_everything(self):
        code = skill_install.main([
            "install", "--target", "all", "--home", self.home,
            "--source", self.source, "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(
            os.path.join(skill_install.canonical_dir(self.home), "SKILL.md")))
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                self.assertTrue(skill_install.is_link(self._link(consumer)))

    def test_both_unchanged_excludes_cline(self):
        code = skill_install.main([
            "install", "--target", "both", "--home", self.home,
            "--source", self.source, "--force"])
        self.assertEqual(code, 0)
        self.assertTrue(skill_install.is_link(
            skill_install.consumer_dir(self.home, "claude")))
        self.assertFalse(os.path.lexists(
            skill_install.consumer_dir(self.home, "cline")))

    def test_verify_covers_both_consumers(self):
        skill_install.main([
            "install", "--target", "all", "--home", self.home,
            "--source", self.source, "--force"])
        problems, notes = skill_install.verify_installation(self.source, self.home)
        self.assertEqual(problems, [])
        self.assertTrue(any("claude: VERIFIED" in n for n in notes))
        self.assertTrue(any("cline: VERIFIED" in n for n in notes))


class UninstallTest(unittest.TestCase):
    CONSUMERS = ("claude", "cline")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fake home ")
        self.home = self.tmp.name
        self.source = make_source(self.tmp.name)
        skill_install.install_canonical(self.source, self.home)
        for consumer in self.CONSUMERS:
            skill_install.ensure_consumer_link(self.home, consumer)

    def tearDown(self):
        self.tmp.cleanup()

    def test_uninstall_consumer_keeps_canonical_and_sibling(self):
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                sibling = "cline" if consumer == "claude" else "claude"
                out = skill_install.uninstall(self.home, consumer)
                self.assertIn("canonical untouched", out)
                self.assertFalse(os.path.lexists(
                    skill_install.consumer_dir(self.home, consumer)))
                self.assertTrue(os.path.isfile(
                    os.path.join(skill_install.canonical_dir(self.home), "SKILL.md")))
                self.assertTrue(skill_install.is_link(
                    skill_install.consumer_dir(self.home, sibling)))
                # restore for next subTest iteration independence
                skill_install.ensure_consumer_link(self.home, consumer)

    def test_uninstall_canonical_requires_force(self):
        with self.assertRaises(skill_install.InstallError):
            skill_install.uninstall(self.home, "agents")
        skill_install.uninstall(self.home, "agents", force=True)
        self.assertFalse(os.path.exists(skill_install.canonical_dir(self.home)))

    def test_uninstall_refuses_physical_consumer(self):
        import shutil
        for consumer in self.CONSUMERS:
            with self.subTest(consumer=consumer):
                link = skill_install.consumer_dir(self.home, consumer)
                skill_install.uninstall(self.home, consumer)  # remove link first
                shutil.copytree(self.source, link)
                with self.assertRaises(skill_install.InstallError):
                    skill_install.uninstall(self.home, consumer)

    def test_uninstall_all_order_links_first(self):
        calls = []
        real_uninstall = skill_install.uninstall

        def spy(home, target, force=False):
            calls.append(target)
            return real_uninstall(home, target, force=force)

        import unittest.mock as mock
        with mock.patch.object(skill_install, "uninstall", side_effect=spy):
            skill_install.main([
                "uninstall", "--target", "all", "--home", self.home, "--force"])
        self.assertEqual(calls, ["claude", "cline", "agents"])


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
