"""Precision fixture suite: genuine vs negative synthetic cases. Stdlib only.

Each case builds small synthetic sessions, runs the real
normalize -> analyze pipeline (CASS export mocked), and asserts the
expected detection behavior. No live history required.
"""
import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "..", "skill", "cass-improve-system", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import analyze
import normalize_cass


def run_pipeline(session_defs):
    """session_defs: list of (session_id, workspace, [(role, content), ...])."""
    inv = {"period": {}, "coverage": {}, "sessions": []}
    exports = {}
    for sid, workspace, messages in session_defs:
        inv["sessions"].append({"path": sid, "agent": "codex",
                                "workspace": workspace, "source_id": "local"})
        exports[sid] = ([{"role": role, "content": text} for role, text in messages],
                        False)

    def fake_export(path, source_id="local", timeout_s=120, retries=3):
        return exports[path]

    with mock.patch.object(normalize_cass, "export_session", side_effect=fake_export):
        norm = normalize_cass.normalize_inventory(inv)
    return analyze.analyze(norm), norm


def finding_types(findings):
    out = {}
    for group in (findings["candidates"], findings["watch_list"],
                  findings["ignored_single_occurrences"]):
        for finding in group:
            out.setdefault(finding["signal_type"], []).append(finding)
    return out


class GenuineCorrectionTest(unittest.TestCase):
    def test_spanish_correction_detected(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "listo, commiteé los cambios"),
            ("user", "NO pases todavía a producción, primero revisá los tests"),
        ])])
        corrections = finding_types(findings).get("user_correction", [])
        self.assertTrue(corrections)

    def test_english_correction_detected(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "done"),
            ("user", "No, that's wrong, I meant the auth module"),
        ])])
        self.assertTrue(finding_types(findings).get("user_correction", []))


class InitialPromptNegativeTest(unittest.TestCase):
    def test_negative_requirement_not_correction(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("user", "Quiero que trabajes con Docker y todavía no sé si usar "
                     "compose; no quiero loguearme en ningún servicio"),
        ])])
        self.assertFalse(finding_types(findings).get("user_correction", []))

    def test_status_report_not_correction(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("user", "todavía no terminé la migración, mañana sigo"),
        ])])
        self.assertFalse(finding_types(findings).get("user_correction", []))


class ToolOutputNoiseTest(unittest.TestCase):
    def test_plan_echo_not_manual_procedure(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "[Tool Output]\n# Plan\nEjecutar manualmente cada paso. "
                          "Hay que copiar cada vez el archivo .env"),
        ])])
        self.assertFalse(finding_types(findings).get("manual_procedure", []))

    def test_health_json_not_promotable(self):
        health = '{"status": "unhealthy", "errors": ["index stale"]}'
        findings, _ = run_pipeline([
            ("s1", "projA", [("assistant", "[Tool Output]\n" + health)]),
            ("s2", "projB", [("assistant", "[Tool Output]\n" + health)]),
        ])
        errors = finding_types(findings).get("error_mention", [])
        self.assertTrue(errors)
        for finding in errors:
            self.assertEqual(finding["confidence"], "low")

    def test_genuine_recurring_build_failure_retained_not_promoted(self):
        sessions = []
        for i, workspace in enumerate(("projA", "projB", "projC")):
            sessions.append((f"s{i}", workspace, [
                ("assistant", "[Tool Output]\nFAILED: auth_test.go:42 timeout"),
            ]))
        findings, _ = run_pipeline(sessions)
        errors = finding_types(findings).get("error_mention", [])
        # Retained as evidence, but pure tool repetition never promotes.
        self.assertTrue(errors)
        for finding in errors:
            self.assertEqual(finding["confidence"], "low")

    def test_recurring_failure_with_user_report_promotes(self):
        sessions = []
        for i, workspace in enumerate(("projA", "projB", "projC")):
            sessions.append((f"s{i}", workspace, [
                ("user", "sigue fallando el auth con timeout"),
                ("assistant", "[Tool Output]\nFAILED: auth_test.go:42 timeout"),
            ]))
        findings, _ = run_pipeline(sessions)
        errors = finding_types(findings).get("error_mention", [])
        self.assertTrue(any(f["confidence"] == "medium" for f in errors))

    def test_user_report_plus_tool_confirmation(self):
        findings, _ = run_pipeline([
            ("s1", "projA", [
                ("user", "sigue fallando el deploy con el mismo error"),
                ("assistant", "[Tool Output]\nFAILED: auth timeout"),
            ]),
            ("s2", "projA", [
                ("user", "otra vez el mismo error de timeout"),
                ("assistant", "[Tool Output]\nFAILED: auth timeout"),
            ]),
        ])
        by_type = finding_types(findings)
        self.assertTrue(by_type.get("error_mention", []))
        self.assertTrue(by_type.get("retry_loop", []))


class ManualProcedureTest(unittest.TestCase):
    def test_documentation_text_not_procedure(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "[Tool Output]\nManual de despliegue: copie el "
                          "archivo cada vez que actualice."),
        ])])
        self.assertFalse(finding_types(findings).get("manual_procedure", []))

    def test_user_friction_detected(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("user", "cada vez tengo que copiar el .env a mano, es tedioso"),
        ])])
        self.assertTrue(finding_types(findings).get("manual_procedure", []))


class RetrySemanticsTest(unittest.TestCase):
    def test_genuine_user_retry(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "deploy failed: timeout"),
            ("user", "sigue fallando, es el mismo error de siempre"),
        ])])
        self.assertTrue(finding_types(findings).get("retry_loop", []))

    def test_copied_log_twice_not_retry(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "[Tool Output]\nFAILED: timeout at step 3"),
            ("assistant", "[Tool Output]\nFAILED: timeout at step 3"),
        ])])
        self.assertFalse(finding_types(findings).get("retry_loop", []))

    def test_documentation_retry_word_not_loop(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("assistant", "See the retry documentation for backoff policy."),
        ])])
        self.assertFalse(finding_types(findings).get("retry_loop", []))

    def test_spanish_retry_phrase(self):
        findings, _ = run_pipeline([("s1", "projA", [
            ("user", "intentalo de nuevo, todavia no funciona"),
        ])])
        self.assertTrue(finding_types(findings).get("retry_loop", []))


class ErrorContextTest(unittest.TestCase):
    def test_task_text_mentioning_errors_suppressed(self):
        for text in ("auditing a documentation repository for FACTUAL ERRORS",
                     "review the error handling section of the docs"):
            with self.subTest(text=text):
                findings, _ = run_pipeline([("s1", "p", [("user", text)])])
                self.assertFalse(finding_types(findings).get("error_mention", []))

    def test_genuine_user_error_reports_detected(self):
        for text in ("me dio un error al guardar",
                     "hay un error en el login",
                     "I got an error when saving",
                     "the build failed with timeout"):
            with self.subTest(text=text):
                findings, _ = run_pipeline([("s1", "p", [("user", text)])])
                self.assertTrue(finding_types(findings).get("error_mention", []))

    def test_tool_failure_still_detected(self):
        findings, _ = run_pipeline([("s1", "p", [
            ("assistant", "[Tool Output]\nFAILED: auth_test.go:42 timeout"),
        ])])
        self.assertTrue(finding_types(findings).get("error_mention", []))


    def test_template_repetition_capped(self):
        template = ("You are auditing a documentation repository for FACTUAL "
                    "ERRORS. Read-only. Do not modify files. " + "x" * 200)
        findings, _ = run_pipeline([
            ("s1", "projA", [("user", template)]),
            ("s2", "projB", [("user", template)]),
            ("s3", "projC", [("user", template)]),
        ])
        for group in (findings["candidates"], findings["watch_list"]):
            self.assertEqual(group, [])

    def test_short_habitual_repetition_exempt(self):
        findings, _ = run_pipeline([
            ("s1", "projA", [("user", "no, eso no es lo que te pedí")]),
            ("s2", "projB", [("user", "no, eso no es lo que te pedí")]),
        ])
        corrections = finding_types(findings).get("user_correction", [])
        self.assertTrue(any(f["confidence"] == "medium" for f in corrections))


class SpanishPairsTest(unittest.TestCase):
    POSITIVES = {
        "user_correction": [
            "no sigas todavía con eso",
            "frená porque rompe el build",
            "no hagas ningún cambio todavía",
            "vos antes me habías dicho que era opcional",
            "me habías dicho que funcionaba",
            "recordá que este proyecto ya tiene linter",
            "eso no es lo que pedí",
            "te confundiste de rama",
            "no era eso",
            "me refería al otro archivo",
        ],
        "retry_loop": [
            "sigue sin funcionar",
            "el mismo error otra vez",
        ],
    }
    NEGATIVES = [
        "no quiero loguearme",
        "no quiero usar Docker",
        "todavía no terminé",
        "quiero que trabajes con compose",
        "recordá esto para mañana",
    ]

    def test_positives(self):
        for stype, texts in self.POSITIVES.items():
            for text in texts:
                with self.subTest(stype=stype, text=text):
                    findings, _ = run_pipeline([("s1", "p", [("user", text)])])
                    self.assertTrue(finding_types(findings).get(stype, []))

    def test_negatives(self):
        for text in self.NEGATIVES:
            with self.subTest(text=text):
                findings, _ = run_pipeline([("s1", "p", [("user", text)])])
                by_type = finding_types(findings)
                self.assertFalse(by_type.get("user_correction", []))


if __name__ == "__main__":
    unittest.main()
