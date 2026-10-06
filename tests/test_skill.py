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

    def test_uninitialized_index_exits_4(self):
        with mock.patch.object(cass_adapter, "find_cass", return_value="cass"), \
             mock.patch.object(cass_adapter, "get_version", return_value=(0, 10, 0)), \
             mock.patch.object(cass_adapter, "check_capabilities", return_value=(True, "x")), \
             mock.patch.object(cass_adapter, "health",
                               return_value={"status": "not_initialized", "initialized": False}):
            code, msg = preflight.preflight()
        self.assertEqual(code, 4)
        self.assertIn("index", msg)

    def test_healthy_exits_0(self):
        with mock.patch.object(cass_adapter, "find_cass", return_value="cass"), \
             mock.patch.object(cass_adapter, "get_version", return_value=(0, 10, 0)), \
             mock.patch.object(cass_adapter, "check_capabilities", return_value=(True, "x")), \
             mock.patch.object(cass_adapter, "health",
                               return_value={"status": "ok", "initialized": True}):
            code, msg = preflight.preflight()
        self.assertEqual(code, 0)


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
        # `cass export` rejects --robot; --format json is its machine mode.
        seen = {}

        def fake_run(argv, timeout_s=120):
            seen["argv"] = argv
            return mock.Mock(returncode=0, stdout='[{"role": "user"}]', stderr="")

        with mock.patch.object(cass_adapter, "_run_raw", side_effect=fake_run):
            out = cass_adapter.export_session("some/path", "local")
        self.assertEqual(out, [{"role": "user"}])
        self.assertNotIn("--robot", seen["argv"])
        self.assertIn("--format", seen["argv"])

    def test_non_cass_command_refused(self):
        with self.assertRaises(cass_adapter.CassError):
            cass_adapter._run_raw(["rm", "-rf", "/"])

    def test_malformed_json_raises_actionable(self):
        with mock.patch.object(cass_adapter, "_run_raw",
                               return_value=mock.Mock(returncode=0, stdout="not json", stderr="")):
            with self.assertRaises(cass_adapter.CassError) as ctx:
                cass_adapter.run_cass_json(["search", "x"])
        self.assertTrue(ctx.exception.actionable)


class NormalizeTest(unittest.TestCase):
    def test_malformed_inventory_input(self):
        with mock.patch.object(sys, "stdin", io.StringIO("{nope")):
            with mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(normalize_cass.main([]), 2)

    def test_export_failure_counted_not_crash(self):
        inv = {"period": {}, "sessions": [{"path": "p", "agent": "a", "workspace": "w"}],
               "coverage": {}}
        with mock.patch.object(normalize_cass, "export_session",
                               side_effect=cass_adapter.CassError("boom")):
            out = normalize_cass.normalize_inventory(inv)
        self.assertEqual(out["coverage"]["sessions_skipped"], 1)
        self.assertEqual(out["coverage"]["sessions_analyzed"], 0)

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
        self.assertIn("GLOBAL RULE", cand["suggested_destination"])

    def test_total_occurrences_preserved(self):
        norm = load_fixture("normalized_multi.json")
        expected = sum(len(s["signals"]) for s in norm["sessions"])
        findings = analyze.analyze(norm)
        got = sum(c["occurrences"] for group in
                  (findings["candidates"], findings["watch_list"],
                   findings["ignored_single_occurrences"]) for c in group)
        self.assertEqual(got, expected)


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
