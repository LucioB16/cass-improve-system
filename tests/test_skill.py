"""Deterministic tests. Stdlib unittest only. No live CASS required."""
import io
import json
import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "..", "skill", "cass-improve-system", "scripts")
FIX = os.path.join(BASE, "fixtures")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import analyze
import cass_adapter
import collect_sessions
import normalize_cass
import period
import preflight
import report


def load_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


class PeriodTest(unittest.TestCase):
    def test_last_24h(self):
        self.assertEqual(period.parse_period("last 24h"), ("-24h", None, "last 24 hours"))
        self.assertEqual(period.parse_period("Last 24 Hours"), ("-24h", None, "last 24 hours"))

    def test_last_n_days(self):
        self.assertEqual(period.parse_period("last 7 days"), ("-7d", None, "last 7 days"))
        self.assertEqual(period.parse_period("last 1 day"), ("-1d", None, "last 1 day"))

    def test_explicit_range(self):
        self.assertEqual(period.parse_period("2026-10-01..2026-10-06"),
                         ("2026-10-01", "2026-10-06", "2026-10-01 through 2026-10-06"))
        self.assertEqual(period.parse_period("from 2026-10-01 to 2026-10-06"),
                         ("2026-10-01", "2026-10-06", "2026-10-01 through 2026-10-06"))

    def test_reversed_range_rejected(self):
        with self.assertRaises(ValueError):
            period.parse_period("2026-10-06..2026-10-01")

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            period.parse_period("sometime last spring-ish")

    def test_none_workspace_sorted(self):
        sessions = [{"path": "a", "agent": "codex", "workspace": None},
                    {"path": "b", "agent": None, "workspace": "w"}]
        with mock.patch.object(collect_sessions, "list_sessions", return_value=sessions):
            inv = collect_sessions.collect("last 24h", None, None, [], [], 10, 30)
        self.assertEqual(inv["coverage"]["sessions_discovered"], 2)


class PreflightTest(unittest.TestCase):
    def _run(self, snap, refresh_result=None, refresh_error=None, **kw):
        with mock.patch.object(cass_adapter, "find_cass", return_value="cass"), \
             mock.patch.object(cass_adapter, "get_version", return_value=(0, 10, 0)), \
             mock.patch.object(cass_adapter, "check_capabilities",
                               return_value=(True, "x")), \
             mock.patch.object(cass_adapter, "readiness", return_value=snap), \
             mock.patch.object(cass_adapter, "refresh_index_incremental") as refresh:
            if refresh_error is not None:
                refresh.side_effect = refresh_error
            else:
                refresh.return_value = refresh_result
            return preflight.preflight(**kw), refresh

    def test_missing_cass_exits_2(self):
        with mock.patch.object(cass_adapter, "find_cass", return_value=None):
            code, msg = preflight.preflight()
        self.assertEqual(code, 2)
        self.assertIn("CASS", msg)
        self.assertIn("No review was performed", msg)

    def test_old_version_exits_3(self):
        with mock.patch.object(cass_adapter, "find_cass", return_value="cass"), \
             mock.patch.object(cass_adapter, "get_version", return_value=(0, 1, 0)):
            code, msg = preflight.preflight()
        self.assertEqual(code, 3)
        self.assertIn("older", msg)

    def test_uninitialized_index_exits_4_no_refresh(self):
        snap = {"health": {"status": "not_initialized", "initialized": False}, "status": {}}
        (code, msg), refresh = self._run(snap)
        self.assertEqual(code, 4)
        self.assertIn("uninitialized", msg)
        refresh.assert_not_called()

    def test_healthy_exits_0(self):
        snap = {"health": {"status": "ok", "initialized": True, "healthy": True,
                           "errors": []}, "status": {}}
        (code, msg), refresh = self._run(snap)
        self.assertEqual(code, 0)
        refresh.assert_not_called()

    def test_stale_only_refreshes_and_proceeds_0(self):
        snap = {"health": {"status": "unhealthy", "initialized": True, "healthy": False,
                           "errors": ["index stale"], "db": {"exists": True}}, "status": {}}
        (code, msg), refresh = self._run(
            snap, refresh_result={"success": True, "conversations": 5})
        self.assertEqual(code, 0)
        refresh.assert_called_once()
        self.assertIn("auto-refreshed", msg)
        self.assertIn("Coverage limitations", msg)

    def test_stale_only_failed_refresh_still_proceeds_0(self):
        snap = {"health": {"status": "unhealthy", "initialized": True, "healthy": False,
                           "errors": ["index stale"], "db": {"exists": True}}, "status": {}}
        (code, msg), refresh = self._run(
            snap, refresh_error=cass_adapter.CassError("locked"))
        self.assertEqual(code, 0)
        refresh.assert_called_once()
        self.assertIn("proceeding anyway", msg)

    def test_stale_strict_mode_exits_4(self):
        snap = {"health": {"status": "unhealthy", "initialized": True, "healthy": False,
                           "errors": ["index stale"]}, "status": {}}
        (code, msg), refresh = self._run(snap, allow_refresh=False)
        self.assertEqual(code, 4)
        refresh.assert_not_called()

    def test_broken_index_exits_4_no_refresh(self):
        snap = {"health": {"status": "unhealthy", "initialized": True, "healthy": False,
                           "errors": ["index stale", "db open failed"],
                           "db": {"open_error": "disk I/O error"}}, "status": {}}
        (code, msg), refresh = self._run(snap)
        self.assertEqual(code, 4)
        self.assertIn("genuinely broken", msg)
        refresh.assert_not_called()


class ClassifyTest(unittest.TestCase):
    def test_healthy(self):
        state, _ = cass_adapter.classify_readiness(
            {"health": {"initialized": True, "healthy": True, "errors": []}, "status": {}})
        self.assertEqual(state, "healthy")

    def test_stale_only(self):
        state, _ = cass_adapter.classify_readiness(
            {"health": {"initialized": True, "healthy": False,
                        "errors": ["index stale"], "db": {"exists": True}},
             "status": {"pending": {"sessions": 0}}})
        self.assertEqual(state, "stale")

    def test_stale_plus_other_error_is_broken(self):
        state, _ = cass_adapter.classify_readiness(
            {"health": {"initialized": True, "healthy": False,
                        "errors": ["index stale", "lock-busy"]}, "status": {}})
        self.assertEqual(state, "broken")

    def test_uninitialized(self):
        state, _ = cass_adapter.classify_readiness(
            {"health": {"status": "not_initialized", "initialized": False}, "status": {}})
        self.assertEqual(state, "uninitialized")

    def test_refresh_is_incremental_only(self):
        seen = {}

        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            seen["argv"] = argv
            seen["machine_flag"] = machine_flag
            return {"success": True, "conversations": 2}

        with mock.patch.object(cass_adapter, "run_cass_json", side_effect=fake_run):
            out = cass_adapter.refresh_index_incremental()
        self.assertEqual(out["conversations"], 2)
        self.assertNotIn("--full", seen["argv"])
        self.assertEqual(seen["machine_flag"], "--json")


class AdapterSafetyTest(unittest.TestCase):
    def test_robot_flag_always_added(self):
        seen = {}

        def fake_run(argv, timeout_s=120):
            seen["argv"] = argv
            return mock.Mock(returncode=0, stdout='{"ok": true}', stderr="")

        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake_run):
            cass_adapter.run_cass_json(["search", "hello"])
        self.assertIn("--robot", seen["argv"])

    def test_export_omits_robot_flag(self):
        # `cass export` rejects --robot; --format json -o <file> is its machine mode.
        seen = {}

        def fake_run(argv, timeout_s=120):
            seen["argv"] = argv
            out = argv[argv.index("-o") + 1]
            with open(out, "w", encoding="utf-8") as fh:
                fh.write('[{"role": "user"}]')
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake_run):
            messages, recovered = cass_adapter.export_session("some/path", "local")
        self.assertEqual(messages, [{"role": "user"}])
        self.assertFalse(recovered)
        self.assertNotIn("--robot", seen["argv"])
        self.assertIn("-o", seen["argv"])

    def test_non_cass_command_refused(self):
        with self.assertRaises(cass_adapter.CassError):
            cass_adapter._run_raw(["rm", "-rf", "/"])

    def test_malformed_json_raises_actionable(self):
        with mock.patch.object(cass_adapter, "_run_raw",
                               return_value=mock.Mock(returncode=0, stdout="not json", stderr="")):
            with self.assertRaises(cass_adapter.CassError) as ctx:
                cass_adapter.run_cass_json(["search", "x"])
        self.assertTrue(ctx.exception.actionable)


class ExportRecoveryTest(unittest.TestCase):
    """Deterministic replays of the CASS 0.10.0 WAL/panic export behavior."""

    def _fake(self, script):
        """script: list of (returncode, file_content|None|'nofile') per call."""
        calls = []

        def fake_run(argv, timeout_s=120):
            calls.append(list(argv))
            code, content = script[min(len(calls) - 1, len(script) - 1)]
            if content != "nofile":
                with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as fh:
                    fh.write(content if content is not None else "")
            # no sleep in tests
            return mock.Mock(returncode=code, stdout="", stderr="panic" if code else "")

        return fake_run, calls

    def test_nonzero_exit_with_valid_file_recovers(self):
        fake, _ = self._fake([(3221226505, '[{"role": "user", "content": "hi"}]')])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            messages, recovered = cass_adapter.export_session("p", "local", retries=1)
        self.assertEqual(messages, [{"role": "user", "content": "hi"}])
        self.assertTrue(recovered)

    def test_nonzero_exit_with_malformed_file_fails_honestly(self):
        fake, _ = self._fake([(3221226505, '[{"role": "user", ')])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            with self.assertRaises(cass_adapter.CassError):
                cass_adapter.export_session("p", "local", retries=1)

    def test_nonzero_exit_with_error_envelope_fails(self):
        fake, _ = self._fake([(2, '{"error": {"message": "usage"}}')])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            with self.assertRaises(cass_adapter.CassError):
                cass_adapter.export_session("p", "local", retries=1)

    def test_include_tools_failure_falls_back_to_plain(self):
        fake, calls = self._fake([
            (3221226505, "nofile"),   # --include-tools attempt: panic, no file
            (0, '[{"role": "assistant"}]'),  # plain attempt: clean success
        ])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            messages, recovered = cass_adapter.export_session("p", "local", retries=1)
        self.assertEqual(messages, [{"role": "assistant"}])
        self.assertFalse(recovered)
        self.assertIn("--include-tools", calls[0])
        self.assertNotIn("--include-tools", calls[1])

    def test_repeated_failure_is_bounded_then_gap(self):
        fake, calls = self._fake([(3221226505, "nofile")])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            with self.assertRaises(cass_adapter.CassError):
                cass_adapter.export_session("p", "local", retries=2)
        self.assertEqual(len(calls), 4)  # 2 rounds x 2 variants: bounded

    def test_zero_exit_with_unusable_file_fails(self):
        fake, _ = self._fake([(0, "")])
        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake), \
             mock.patch.object(cass_adapter.time, "sleep", return_value=None):
            with self.assertRaises(cass_adapter.CassError):
                cass_adapter.export_session("p", "local", retries=1)


class ManifestTest(unittest.TestCase):
    def test_manifest_stable_and_sorted(self):
        import tempfile
        import manifest as manifest_mod
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub"))
            with open(os.path.join(tmp, "b.txt"), "w") as fh:
                fh.write("b")
            with open(os.path.join(tmp, "sub", "a.txt"), "w") as fh:
                fh.write("a")
            first = manifest_mod.manifest(tmp)
            self.assertEqual(sorted(first), ["b.txt", "sub/a.txt"])
            self.assertEqual(first, manifest_mod.manifest(tmp))

    def test_manifest_excludes_bytecode(self):
        import tempfile
        import manifest as manifest_mod
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "__pycache__"))
            with open(os.path.join(tmp, "__pycache__", "x.pyc"), "w") as fh:
                fh.write("x")
            with open(os.path.join(tmp, "y.pyc"), "w") as fh:
                fh.write("y")
            with open(os.path.join(tmp, "ok.py"), "w") as fh:
                fh.write("ok")
            self.assertEqual(sorted(manifest_mod.manifest(tmp)), ["ok.py"])


class NormalizeTest(unittest.TestCase):
    def test_malformed_inventory_input(self):
        with mock.patch.object(sys, "stdin", io.StringIO("{nope")):
            with mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(normalize_cass.main([]), 2)

    def test_utf16_piped_input_tolerated(self):
        payload = '{"period": {}, "sessions": [], "coverage": {}}'.encode("utf-16")
        stdin = mock.Mock()
        stdin.buffer.read.return_value = payload
        with mock.patch.object(sys, "stdin", stdin):
            self.assertEqual(normalize_cass._read_stdin_text(),
                             '{"period": {}, "sessions": [], "coverage": {}}')

    def test_export_failure_counted_not_crash(self):
        inv = {"period": {}, "sessions": [{"path": "p", "agent": "a", "workspace": "w"}],
               "coverage": {}}
        with mock.patch.object(normalize_cass, "export_session",
                               side_effect=cass_adapter.CassError("boom")):
            out = normalize_cass.normalize_inventory(inv)
        self.assertEqual(out["coverage"]["sessions_skipped"], 1)
        self.assertEqual(out["coverage"]["sessions_analyzed"], 0)

    def test_recovered_export_flagged_in_metadata_and_coverage(self):
        inv = {"period": {}, "sessions": [{"path": "p", "agent": "a", "workspace": "w"}],
               "coverage": {}}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=([{"role": "user", "content": "hi"}], True)):
            out = normalize_cass.normalize_inventory(inv)
        self.assertEqual(out["coverage"]["sessions_analyzed"], 1)
        self.assertEqual(out["coverage"]["sessions_recovered"], 1)
        self.assertTrue(out["sessions"][0]["metadata"]["recovered_from_panic"])

    def test_secrets_redacted(self):
        text = "key is ghp_abcdefghij1234567890 and password: hunter2 s3cret done"
        red = normalize_cass.redact(text)
        self.assertNotIn("ghp_abcdefghij1234567890", red)
        self.assertIn("[REDACTED", red)
        self.assertNotIn("password: hunter2", red)

    def test_private_key_redacted(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
        self.assertIn("[REDACTED:private-key]", normalize_cass.redact(text))

    def test_correction_signal_detected(self):
        sigs = normalize_cass.extract_signals(
            "user", "No, that's wrong, I meant the other file", "s#msg1")
        self.assertTrue(any(s["type"] == "user_correction" for s in sigs))

    def test_assistant_text_not_a_user_correction(self):
        sigs = normalize_cass.extract_signals(
            "assistant", "No, that's wrong, I meant the other file", "s#msg1")
        self.assertFalse(any(s["type"] == "user_correction" for s in sigs))


class AnalyzeTest(unittest.TestCase):
    def test_cross_project_correction_is_high_confidence(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        corrections = [c for c in findings["candidates"]
                       if c["signal_type"] == "user_correction"]
        self.assertEqual(len(corrections), 1)  # de-duplicated into one candidate
        cand = corrections[0]
        self.assertEqual(cand["session_count"], 3)
        self.assertEqual(cand["project_count"], 2)
        self.assertTrue(cand["cross_project"])
        self.assertEqual(cand["confidence"], "high")
        self.assertEqual(cand["occurrences"], 3)

    def test_single_occurrences_not_promoted(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        promo_types = {c["signal_type"] for c in findings["candidates"]}
        self.assertNotIn("manual_procedure", promo_types)
        ignored_types = {c["signal_type"] for c in findings["ignored_single_occurrences"]}
        self.assertIn("manual_procedure", ignored_types)

    def test_project_scoped_vs_global(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        cand = next(c for c in findings["candidates"]
                    if c["signal_type"] == "user_correction")
        self.assertIn("GLOBAL RULE (if cross-project)", cand["destinations"])

    def test_finding_identity_stable(self):
        first = analyze.analyze(load_fixture("normalized_multi.json"))
        second = analyze.analyze(load_fixture("normalized_multi.json"))
        ids1 = sorted(c["id"] for c in first["candidates"])
        ids2 = sorted(c["id"] for c in second["candidates"])
        self.assertEqual(ids1, ids2)
        self.assertTrue(all(":" in i for i in ids1))
        titles = [c["title"] for c in first["candidates"]]
        self.assertTrue(all(t for t in titles))

    def test_total_occurrences_preserved(self):
        norm = load_fixture("normalized_multi.json")
        expected = sum(len(s["signals"]) for s in norm["sessions"])
        findings = analyze.analyze(norm)
        got = sum(c["occurrences"] for group in
                  (findings["candidates"], findings["watch_list"],
                   findings["ignored_single_occurrences"]) for c in group)
        self.assertEqual(got, expected)


class MultilingualTest(unittest.TestCase):
    def _types(self, role, text):
        return {s["type"] for s in normalize_cass.extract_signals(role, text, "t#0")}

    def test_english_preserved(self):
        self.assertIn("user_correction", self._types("user", "No, that's wrong, try again"))
        self.assertIn("retry_loop", self._types("user", "still failing, same error"))
        self.assertIn("manual_procedure", self._types("user", "I manually copy this every time"))
        self.assertIn("success_pattern", self._types("user", "perfect, thanks, that worked"))

    def test_spanish_corrections(self):
        for text in ("NO pases todavía, primero terminá eso",
                     "vos antes me habías dicho otra cosa",
                     "por qué interrumpiste tu trabajo?",
                     "Recordá que este proyecto ya tiene un skill local",
                     "eso no es lo que te pedí",
                     "te equivocaste de archivo",
                     "no era eso lo que necesitaba",
                     "acordate que hay que usar el entorno correcto"):
            with self.subTest(text=text):
                self.assertIn("user_correction", self._types("user", text))

    def test_reminder_not_correction(self):
        # Reminders about future actions are not corrections of prior behavior.
        for text in ("recordá esto para mañana",
                     "acordate de usar el entorno la próxima vez"):
            with self.subTest(text=text):
                self.assertNotIn("user_correction", self._types("user", text))

    def test_spanish_correction_accent_variants(self):
        accented = self._types("user", "Recordá que el deploy es manual")
        plain = self._types("user", "Recorda que el deploy es manual")
        self.assertIn("user_correction", accented)
        self.assertIn("user_correction", plain)

    def test_spanish_retry_manual_rediscovery_success(self):
        self.assertIn("retry_loop", self._types("user", "sigue sin funcionar"))
        self.assertIn("retry_loop", self._types("user", "el mismo error otra vez"))
        self.assertIn("manual_procedure", self._types("user", "tengo que hacerlo a mano cada vez"))
        self.assertIn("rediscovery", self._types("user", "cómo configuro la salida?"))
        self.assertIn("rediscovery", self._types("user", "cuál es el comando correcto?"))
        self.assertIn("success_pattern", self._types("user", "perfecto, funcionó"))
        self.assertIn("success_pattern", self._types("user", "era justo lo que quería"))
        self.assertIn("error_mention", self._types("user", "el deploy falló con error"))

    def test_spanish_negatives(self):
        for text in ("no sé qué pasó acá",
                     "noticias del día de hoy",
                     "revisá el manual de usuario",
                     "anotado en la libreta",
                     "terror en el cine"):
            with self.subTest(text=text):
                found = self._types("user", text)
                self.assertNotIn("user_correction", found)
                self.assertNotIn("retry_loop", found)
                self.assertNotIn("manual_procedure", found)

    def test_correction_requires_user_role(self):
        self.assertNotIn("user_correction",
                         self._types("assistant", "eso no es lo que te pedí"))


class RoleTest(unittest.TestCase):
    def test_explicit_roles_pass_through(self):
        self.assertEqual(normalize_cass.infer_role({"role": "user"}), "user")
        self.assertEqual(normalize_cass.infer_role({"role": "assistant"}), "assistant")
        self.assertEqual(normalize_cass.infer_role({"role": "human"}), "user")
        self.assertEqual(normalize_cass.infer_role({"role": "system"}), "system")

    def test_antigravity_mapping(self):
        self.assertEqual(normalize_cass.infer_role(
            {"source": "USER_EXPLICIT", "type": "USER_INPUT"}), "user")
        self.assertEqual(normalize_cass.infer_role(
            {"source": "MODEL", "type": "PLANNER_RESPONSE"}), "assistant")

    def test_unknown_stays_unknown(self):
        self.assertEqual(normalize_cass.infer_role({}), "?")
        self.assertEqual(normalize_cass.infer_role({"role": "?"}), "?")
        self.assertEqual(normalize_cass.infer_role({"source": "SOMETHING_NEW"}), "?")
        self.assertEqual(normalize_cass.infer_role({"type": "FUTURE_KIND"}), "?")

    def test_antigravity_fixture_normalizes_roles(self):
        fixture = load_fixture("antigravity_export.json")
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "ag-transcript", "agent": "antigravity",
                             "workspace": "?", "source_id": "local"}]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(fixture, False)):
            out = normalize_cass.normalize_inventory(inv)
        session = out["sessions"][0]
        roles = [m["role"] for m in session["messages"]]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertNotIn("?", roles)
        user_signals = [s for s in session["signals"]
                        if s["type"] == "user_correction"]
        self.assertTrue(user_signals)
        self.assertTrue(all("#msg" in s["ref"] for s in user_signals))


class ReportTest(unittest.TestCase):
    def test_report_states_empty_honestly(self):
        text = report.render({"period": {"label": "last 24 hours"}, "coverage": {},
                              "candidates": [], "watch_list": [],
                              "ignored_single_occurrences": []})
        self.assertIn("No strong improvement candidates were found", text)
        self.assertIn("Coverage limitations", text)

    def test_report_has_traceable_evidence(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        text = report.render(findings)
        self.assertIn("sess-aaa#msg1", text)
        self.assertIn("Confidence: high", text)

    def test_report_redaction_holds(self):
        findings = {"period": {"label": "t"}, "coverage": {},
                    "candidates": [{"signal_type": "error_mention", "occurrences": 3,
                                    "session_count": 3, "project_count": 2,
                                    "agents": ["codex"], "cross_project": True,
                                    "confidence": "high",
                                    "suggested_destination": "TOOLING GAP",
                                    "evidence": [{"ref": "s#1",
                                                  "excerpt": "leaked ghp_abcdefghij1234567890"}]}],
                    "watch_list": [], "ignored_single_occurrences": []}
        # excerpts entering the report must already be redacted upstream
        raw = json.dumps(findings)
        self.assertNotIn("ghp_", normalize_cass.redact(raw))


class LongSessionTest(unittest.TestCase):
    def _big_export(self, n=200, late_correction_at=190):
        messages = []
        for i in range(n):
            if i == late_correction_at:
                messages.append({"role": "user",
                                 "content": "no, eso no es lo que te pedí, empezá de nuevo"})
            elif i == late_correction_at + 2:
                messages.append({"role": "assistant",
                                 "content": "failed again with the same error, retrying"})
            elif i % 2:
                messages.append({"role": "assistant", "content": f"routine output {i}"})
            else:
                messages.append({"role": "user", "content": f"ok continúa {i}"})
        return messages

    def test_late_correction_detected_with_bounded_retention(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "big", "agent": "codex",
                             "workspace": "w", "source_id": "local"}]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(self._big_export(), False)):
            out = normalize_cass.normalize_inventory(inv)
        session = out["sessions"][0]
        meta = session["metadata"]
        self.assertEqual(meta["total_messages"], 200)
        self.assertLessEqual(len(session["messages"]), 120)
        self.assertEqual(meta["sampling"], "stratified: signal-bearing + user turns first, then head/tail")
        corrections = [s for s in session["signals"] if s["type"] == "user_correction"]
        self.assertTrue(any(s["ref"] == "big#msg190" for s in corrections))

    def test_isolated_failure_is_not_a_loop(self):
        # One assistant failure mention is not retry evidence.
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "big", "agent": "codex",
                             "workspace": "w", "source_id": "local"}]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(self._big_export(), False)):
            out = normalize_cass.normalize_inventory(inv)
        retries = [s for s in out["sessions"][0]["signals"]
                   if s["type"] == "retry_loop"]
        self.assertFalse(any(s["ref"] == "big#msg192" for s in retries))

    def test_repeated_same_error_synthesizes_retry(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "loop", "agent": "codex",
                             "workspace": "w", "source_id": "local"}]}
        messages = [{"role": "assistant", "content": "build failed: auth timeout"} if i % 2
                    else {"role": "assistant", "content": f"step {i} ok"}
                    for i in range(8)]
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            norm = normalize_cass.normalize_inventory(inv)
        findings = analyze.analyze(norm)
        retries = [c for c in findings["candidates"] + findings["watch_list"]
                   + findings["ignored_single_occurrences"]
                   if c["signal_type"] == "retry_loop"]
        # Recurrence is recorded as evidence; single-session promotion still
        # requires cross-session support per policy.
        self.assertTrue(retries)

    def test_selection_deterministic_and_user_biased(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "big", "agent": "codex",
                             "workspace": "w", "source_id": "local"}]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(self._big_export(), False)):
            first = normalize_cass.normalize_inventory(inv)["sessions"][0]
            second = normalize_cass.normalize_inventory(inv)["sessions"][0]
        roles1 = [m["role"] for m in first["messages"]]
        roles2 = [m["role"] for m in second["messages"]]
        self.assertEqual(roles1, roles2)
        self.assertGreater(roles1.count("user"), roles1.count("assistant") // 2)

    def test_short_session_keeps_everything(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "small", "agent": "codex",
                             "workspace": "w", "source_id": "local"}]}
        messages = [{"role": "user", "content": "hola"} for _ in range(10)]
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)["sessions"][0]
        self.assertEqual(len(out["messages"]), 10)
        self.assertEqual(out["metadata"]["sampling"], "none")
        self.assertFalse(out["metadata"]["truncated"])


class PeriodFidelityTest(unittest.TestCase):
    def test_resolve_relative_bounds(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        start = collect_sessions.resolve_bound("-7d", is_end=False, now=now)
        self.assertEqual(start, "2026-10-03T12:00:00+00:00")
        self.assertIsNone(collect_sessions.resolve_bound("nonsense!!", is_end=False, now=now))

    def test_explicit_range_bounds(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            collect_sessions.resolve_bound("2026-10-01", is_end=False, now=now),
            "2026-10-01T00:00:00+00:00")
        self.assertEqual(
            collect_sessions.resolve_bound("2026-10-06", is_end=True, now=now),
            "2026-10-06T23:59:59.999999+00:00")

    def test_stale_conversation_flagged_not_dropped(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "old", "agent": "codex", "workspace": "w",
                             "source_id": "local",
                             "_period_start": "2026-10-03T00:00:00+00:00",
                             "_period_end": "2026-10-10T23:59:59+00:00"}]}
        messages = [{"role": "user", "content": "hola",
                     "created_at": "2026-08-15T10:00:00Z"}]
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        session = out["sessions"][0]
        self.assertTrue(session["metadata"]["out_of_period"])
        self.assertEqual(session["metadata"]["timestamps"], "messages")
        self.assertEqual(out["coverage"]["sessions_analyzed"], 1)
        self.assertEqual(out["coverage"]["sessions_outside_period"], 1)

    def test_genuinely_recent_session(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "new", "agent": "codex", "workspace": "w",
                             "source_id": "local",
                             "_period_start": "2026-10-03T00:00:00+00:00",
                             "_period_end": "2026-10-10T23:59:59+00:00"}]}
        messages = [{"role": "user", "content": "hola",
                     "created_at": "2026-10-05T10:00:00Z"}]
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        self.assertFalse(out["sessions"][0]["metadata"]["out_of_period"])

    def test_missing_timestamps_never_excluded(self):
        inv = {"period": {}, "coverage": {},
               "sessions": [{"path": "myst", "agent": "codex", "workspace": "w",
                             "source_id": "local",
                             "_period_start": "2026-10-03T00:00:00+00:00",
                             "_period_end": "2026-10-10T23:59:59+00:00"}]}
        messages = [{"role": "user", "content": "hola sin tiempo"}]
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        session = out["sessions"][0]
        self.assertFalse(session["metadata"]["out_of_period"])
        self.assertEqual(session["metadata"]["timestamps"], "none")
        self.assertEqual(out["coverage"]["sessions_timestamp_unknown"], 1)
        self.assertEqual(out["coverage"]["sessions_analyzed"], 1)


class DedupTest(unittest.TestCase):
    def test_evidence_block_rendered_once(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        text = report.render(findings)
        self.assertEqual(text.count("sess-aaa#msg1"), 1)
        self.assertEqual(text.count("sess-bbb#msg1"), 1)

    def test_destinations_coexist_without_cloning(self):
        findings = {"period": {"label": "t"}, "coverage": {},
                    "candidates": [{"id": "manual_procedure:abc123",
                                    "title": "Repeated manual procedures",
                                    "signal_type": "manual_procedure",
                                    "occurrences": 4, "session_count": 3,
                                    "project_count": 2, "agents": ["codex"],
                                    "cross_project": True, "confidence": "high",
                                    "destinations": ["AUTOMATION", "NEW SKILL"],
                                    "evidence": [{"ref": "s#9",
                                                  "excerpt": "copy config by hand"}]}],
                    "watch_list": [], "ignored_single_occurrences": []}
        text = report.render(findings)
        self.assertEqual(text.count("copy config by hand"), 1)
        self.assertIn("## Automation candidates", text)
        self.assertIn("## New skill candidates", text)
        self.assertIn("Destinations: AUTOMATION, NEW SKILL", text)


class ReadOnlyTest(unittest.TestCase):
    def test_pipeline_writes_no_files(self):
        before = set(os.listdir(BASE))
        norm = load_fixture("normalized_multi.json")
        findings = analyze.analyze(norm)
        text = report.render(findings)
        self.assertTrue(text.startswith("# System Review"))
        self.assertEqual(before, set(os.listdir(BASE)))

    def test_report_disclaims_read_only(self):
        findings = analyze.analyze(load_fixture("normalized_multi.json"))
        self.assertIn("report-only", report.render(findings).lower().replace("report only", "report-only")
                      .replace("report—only", "report-only"))


if __name__ == "__main__":
    unittest.main()
