"""Copilot Chronicle compatibility: deterministic synthetic tests. Stdlib only.

Fixtures mirror the real `events.jsonl` envelope ({type, data, timestamp})
verified through live `cass export --format json` against a synthetic
Chronicle file: raw events, nested data.content, no top-level role/content.
No live user history required; no real conversations committed.
"""
import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, "..", "skill", "cass-improve-system", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import analyze
import cass_adapter
import collect_sessions
import normalize_cass
import report


def copilot_session(sid="copilot-s1", workspace="demo", events=None,
                    agent="copilot_cli"):
    """A synthetic Copilot session inventory entry plus its raw export."""
    if events is None:
        events = [
            {"type": "session.start",
             "data": {"sessionId": "uuid-1", "cwd": "C:/proj/demo"},
             "timestamp": "2026-10-10T10:00:00.000Z"},
            {"type": "user.message",
             "data": {"content": "I have to do this manually every time."},
             "timestamp": "2026-10-10T10:01:00.000Z"},
            {"type": "assistant.message",
             "data": {"content": "You can automate it with a script."},
             "timestamp": "2026-10-10T10:01:05.000Z"},
            {"type": "user.message",
             "data": {"content": "That is not what I asked. Please correct it."},
             "timestamp": "2026-10-10T10:02:00.000Z"},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "output": "FAILED: timeout at step 3"},
             "timestamp": "2026-10-10T10:02:09.000Z"},
            {"type": "session.shutdown",
             "data": {"reason": "done"},
             "timestamp": "2026-10-10T10:03:00.000Z"},
        ]
    entry = {"path": sid, "agent": agent, "workspace": workspace,
             "source_id": "local",
             "_period_start": "2026-10-09T00:00:00+00:00",
             "_period_end": "2026-10-11T23:59:59+00:00"}
    return entry, events


def run_pipeline(defs):
    """defs: list of (entry, export_messages). Full normalize->analyze."""
    inv = {"period": {"label": "t"}, "coverage": {}, "sessions": []}
    exports = {}
    for entry, messages in defs:
        inv["sessions"].append(entry)
        exports[entry["path"]] = (messages, False)

    def fake_export(path, source_id="local", timeout_s=120, retries=3):
        return exports[path]

    with mock.patch.object(normalize_cass, "export_session",
                           side_effect=fake_export):
        norm = normalize_cass.normalize_inventory(inv)
    return analyze.analyze(norm), norm


def finding_types(findings):
    out = {}
    for group in (findings["candidates"], findings["watch_list"],
                  findings["ignored_single_occurrences"]):
        for finding in group:
            out.setdefault(finding["signal_type"], []).append(finding)
    return out


class CopilotRolesTest(unittest.TestCase):
    def test_user_message_maps_to_user(self):
        self.assertEqual(
            normalize_cass.infer_role({"type": "user.message",
                                       "data": {"content": "hi"}}), "user")

    def test_assistant_message_maps_to_assistant(self):
        self.assertEqual(
            normalize_cass.infer_role({"type": "assistant.message",
                                       "data": {"content": "hi"}}), "assistant")

    def test_tool_events_map_to_tool(self):
        for etype in ("tool.execution_start", "tool.execution_complete"):
            with self.subTest(etype=etype):
                self.assertEqual(
                    normalize_cass.infer_role({"type": etype, "data": {}}),
                    "tool")

    def test_lifecycle_events_map_to_system(self):
        for etype in ("session.start", "session.model_change",
                      "session.task_complete", "session.shutdown",
                      "assistant.turn_start", "assistant.turn_end",
                      "system.message"):
            with self.subTest(etype=etype):
                self.assertEqual(
                    normalize_cass.infer_role({"type": etype, "data": {}}),
                    "system")

    def test_unknown_event_stays_unknown(self):
        self.assertEqual(normalize_cass.infer_role(
            {"type": "copilot.something_new", "data": {}}), "?")
        self.assertEqual(normalize_cass.infer_role(
            {"type": "USER.MESSAGE"}), "user")  # case-insensitive

    def test_explicit_role_wins(self):
        self.assertEqual(
            normalize_cass.infer_role({"role": "assistant",
                                       "type": "user.message",
                                       "data": {"content": "x"}}), "assistant")

    def test_antigravity_still_works(self):
        self.assertEqual(
            normalize_cass.infer_role({"source": "USER_EXPLICIT",
                                       "type": "USER_INPUT"}), "user")
        self.assertEqual(
            normalize_cass.infer_role({"source": "MODEL",
                                       "type": "PLANNER_RESPONSE"}), "assistant")


class CopilotContentTest(unittest.TestCase):
    def test_nested_data_content(self):
        self.assertEqual(
            normalize_cass.extract_text(
                {"type": "user.message", "data": {"content": "hello"}}),
            "hello")

    def test_top_level_content_preferred(self):
        self.assertEqual(
            normalize_cass.extract_text(
                {"role": "user", "content": "top",
                 "data": {"content": "nested"}}), "top")

    def test_missing_data_object(self):
        self.assertEqual(
            normalize_cass.extract_text({"type": "user.message"}), "")

    def test_null_and_empty_content(self):
        self.assertEqual(
            normalize_cass.extract_text(
                {"type": "user.message",
                 "data": {"content": None}}), "")
        self.assertEqual(
            normalize_cass.extract_text(
                {"type": "user.message", "data": {"content": "  "}}), "")

    def test_structured_content_blocks(self):
        msg = {"type": "assistant.message",
               "data": {"content": [{"type": "text", "text": "first"},
                                    {"type": "image", "source": "..."},
                                    {"type": "text", "text": "second"}]}}
        self.assertEqual(normalize_cass.extract_text(msg), "first\nsecond")

    def test_tool_payload_keys(self):
        msg = {"type": "tool.execution_complete",
               "data": {"toolCallId": "t1", "output": "FAILED: x"}}
        self.assertEqual(normalize_cass.extract_text(msg), "FAILED: x")

    def test_no_envelope_dump(self):
        msg = {"type": "weird.kind",
               "data": {"blob": {"a": 1}, "content": None},
               "meta": {"x": "y"}}
        text = normalize_cass.extract_text(msg)
        self.assertNotIn("blob", text)
        self.assertNotIn("weird.kind", text)

    def test_unicode_spanish_content(self):
        msg = {"type": "user.message",
               "data": {"content": "¿por qué se rompió? todavía no funciona 😖"}}
        self.assertIn("rompi", normalize_cass.extract_text(msg))

    def test_malformed_event_safe(self):
        self.assertEqual(normalize_cass.extract_text("not-a-dict"), "")
        self.assertEqual(normalize_cass.extract_text(None), "")

    def test_sensitive_strings_redacted(self):
        msg = {"type": "user.message",
               "data": {"content": "my key is ghp_abcdefghij1234567890 done"}}
        entry, messages = copilot_session(events=[msg])
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        for m in out["sessions"][0]["messages"]:
            self.assertNotIn("ghp_abcdefghij1234567890", m["excerpt"])

    def test_timestamps_preserved(self):
        entry, messages = copilot_session()
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        meta = out["sessions"][0]["metadata"]
        self.assertEqual(meta["timestamps"], "messages")
        self.assertTrue(meta["activity_start"].startswith("2026-10-10T10:00"))
        self.assertTrue(meta["activity_end"].startswith("2026-10-10T10:03"))
        self.assertFalse(meta["out_of_period"])

    def test_message_indices_stable(self):
        entry, messages = copilot_session()
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        for signal in out["sessions"][0]["signals"]:
            self.assertRegex(signal["ref"], r"#msg\d+$")

    def test_long_session_bounded(self):
        events = [{"type": "session.start", "data": {},
                   "timestamp": "2026-10-10T10:00:00.000Z"}]
        for i in range(300):
            events.append({"type": "user.message",
                           "data": {"content": f"note number {i}"},
                           "timestamp": "2026-10-10T10:01:00.000Z"})
        entry, messages = copilot_session(events=events)
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        session = out["sessions"][0]
        self.assertLessEqual(len(session["messages"]), 120)
        self.assertEqual(session["metadata"]["total_messages"], 301)


class CopilotSignalsTest(unittest.TestCase):
    def test_known_user_signals_detected(self):
        entry, messages = copilot_session()
        findings, _ = run_pipeline([(entry, messages)])
        by_type = finding_types(findings)
        self.assertTrue(by_type.get("manual_procedure", []))
        manual = by_type["manual_procedure"][0]
        self.assertIn("user_text", manual["provenance"])

    def test_tool_phrase_not_user_correction(self):
        entry, messages = copilot_session(events=[
            {"type": "user.message",
             "data": {"content": "check the build"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
            {"type": "tool.execution_complete",
             "data": {"output": "that is not what I asked, fix the config"},
             "timestamp": "2026-10-10T10:02:00.000Z"},
        ])
        findings, _ = run_pipeline([(entry, messages)])
        self.assertFalse(finding_types(findings).get("user_correction", []))

    def test_injected_directive_not_user(self):
        entry, messages = copilot_session(events=[
            {"type": "user.message",
             "data": {"content": "[SYSTEM DIRECTIVE: do the thing]"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        self.assertEqual(finding_types(findings), {})
        session = norm["sessions"][0]
        self.assertEqual(session["metadata"]["content_coverage"], "empty")

    def test_metadata_not_indexed_as_user(self):
        entry, messages = copilot_session(events=[
            {"type": "session.start",
             "data": {"sessionId": "uuid-9", "cwd": "C:/x"},
             "timestamp": "2026-10-10T10:00:00.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        self.assertEqual(finding_types(findings), {})
        session = norm["sessions"][0]
        roles = [m["role"] for m in session["messages"]]
        self.assertNotIn("user", roles)

    def test_valid_conversation_no_signals_is_valid(self):
        entry, messages = copilot_session(events=[
            {"type": "user.message",
             "data": {"content": "what time is it"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
            {"type": "assistant.message",
             "data": {"content": "time to write code"},
             "timestamp": "2026-10-10T10:01:05.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        self.assertEqual(finding_types(findings), {})
        self.assertEqual(
            norm["sessions"][0]["metadata"]["content_coverage"], "full")


class CopilotCoverageTest(unittest.TestCase):
    def test_unrecognized_format_flagged(self):
        entry, messages = copilot_session(events=[
            {"type": "copilot.future_kind",
             "data": {"content": "hello from tomorrow"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        meta = norm["sessions"][0]["metadata"]
        self.assertEqual(meta["content_coverage"], "none")
        self.assertEqual(meta["unknown_role_count"], 1)
        self.assertEqual(
            norm["coverage"]["sessions_content_none"], 1)
        self.assertEqual(finding_types(findings), {})

    def test_partial_decoding(self):
        entry, messages = copilot_session(events=[
            {"type": "user.message",
             "data": {"content": "hello there"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
            {"type": "copilot.future_kind",
             "data": {"content": "mystery"},
             "timestamp": "2026-10-10T10:01:05.000Z"},
        ])
        _, norm = run_pipeline([(entry, messages)])
        meta = norm["sessions"][0]["metadata"]
        self.assertEqual(meta["content_coverage"], "partial")
        self.assertEqual(meta["interpretable_messages"], 1)

    def test_control_only_is_empty_not_failure(self):
        entry, messages = copilot_session(events=[
            {"type": "session.start", "data": {"sessionId": "u"},
             "timestamp": "2026-10-10T10:00:00.000Z"},
            {"type": "session.shutdown", "data": {"reason": "done"},
             "timestamp": "2026-10-10T10:03:00.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        self.assertEqual(
            norm["sessions"][0]["metadata"]["content_coverage"], "empty")
        self.assertEqual(
            norm["coverage"]["sessions_content_none"], 0)
        self.assertEqual(finding_types(findings), {})

    def test_malformed_payload_safe(self):
        entry = {"path": "bad", "agent": "copilot_cli", "workspace": "?",
                 "source_id": "local",
                 "_period_start": "2026-10-09T00:00:00+00:00",
                 "_period_end": "2026-10-11T23:59:59+00:00"}
        messages = ["not-a-dict", {"type": "user.message",
                                   "data": {"content": 42},
                                   "timestamp": "2026-10-10T10:01:00.000Z"}]
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               return_value=(messages, False)):
            out = normalize_cass.normalize_inventory(inv)
        meta = out["sessions"][0]["metadata"]
        self.assertEqual(meta["raw_event_count"], 2)
        self.assertEqual(meta["total_messages"], 1)

    def test_export_failure_still_gap(self):
        entry = {"path": "gone", "agent": "copilot_cli", "workspace": "?",
                 "source_id": "local"}
        inv = {"period": {}, "coverage": {}, "sessions": [entry]}
        with mock.patch.object(normalize_cass, "export_session",
                               side_effect=cass_adapter.CassError("boom")):
            out = normalize_cass.normalize_inventory(inv)
        self.assertEqual(out["coverage"]["sessions_skipped"], 1)

    def test_report_warns_on_content_none(self):
        findings = {"period": {"label": "t"},
                    "coverage": {"sessions_discovered": 1,
                                 "sessions_analyzed": 1,
                                 "sessions_skipped": 0,
                                 "agents": ["copilot_cli"],
                                 "workspaces": ["?"],
                                 "sessions_content_none": 1,
                                 "sessions_content_partial": 0},
                    "candidates": [], "watch_list": [],
                    "ignored_single_occurrences": []}
        import report as report_mod
        text = report_mod.render(findings)
        self.assertIn("no interpretable content", text)
        self.assertIn("does NOT mean those sessions were empty", text)


class CopilotCliFilterTest(unittest.TestCase):
    def _fake(self, sessions_payload, timeline_payload=None):
        calls = []

        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            calls.append(list(argv))
            if argv[0] == "timeline":
                return timeline_payload
            assert argv[0] == "sessions", argv
            assert "--agent" not in argv, argv  # never to sessions
            return sessions_payload

        return fake_run, calls

    def test_agent_filter_unions_timeline_and_sessions(self):
        tl = {"total_sessions": 1, "sessions": [
            {"source_path": "copilot-events", "agent": "copilot_cli",
             "workspace": None, "source_id": "local", "title": "t",
             "started_at": None, "ended_at": None, "message_count": 3}]}
        fake, calls = self._fake({"sessions": []}, tl)
        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake):
            sessions, info = cass_adapter.fetch_inventory(
                since="-24h", limit=10, agent=["copilot_cli"])
        self.assertEqual(info["via"], "sessions+timeline")
        self.assertTrue(info["complete"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["agent"], "copilot_cli")
        self.assertEqual(sessions[0]["path"], "copilot-events")
        kinds = sorted(c[0] for c in calls)
        self.assertEqual(kinds, ["sessions", "timeline"])

    def test_union_keeps_sessions_timeline_misses(self):
        # Live-observed case: timeline (start-time semantics) misses long
        # sessions that sessions (activity semantics) finds. Union keeps both.
        tl = {"total_sessions": 0, "sessions": []}
        fake, _ = self._fake({"sessions": [
            {"path": "opencode-live", "agent": "opencode",
             "workspace": "w", "modified": "2026-10-10T10:00:00+00:00"}]}, tl)
        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake):
            sessions, info = cass_adapter.fetch_inventory(
                since="-24h", limit=10, agent=["opencode"])
        self.assertTrue(info["complete"])
        self.assertEqual([s["path"] for s in sessions], ["opencode-live"])

    def test_no_agent_filter_uses_sessions_path(self):
        fake, calls = self._fake({"sessions": [{"path": "a"}]})
        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake):
            sessions, info = cass_adapter.fetch_inventory(
                since="-24h", limit=10)
        self.assertEqual(info["via"], "sessions")
        self.assertEqual(len(sessions), 1)

    def test_workspace_filter_uses_sessions_server_flag(self):
        seen = []

        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            seen.append(list(argv))
            if argv[0] == "timeline":
                return {"total_sessions": 0, "sessions": []}
            return {"sessions": []}

        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake_run):
            cass_adapter.fetch_inventory(since="-24h", limit=10,
                                         agent=["codex"],
                                         workspace=["C:/proj"])
        sessions_calls = [a for a in seen if a[0] == "sessions"]
        timeline_calls = [a for a in seen if a[0] == "timeline"]
        self.assertTrue(sessions_calls)
        self.assertTrue(timeline_calls)
        for argv in sessions_calls:
            self.assertNotIn("--agent", argv)
        self.assertIn("--workspace", sessions_calls[0])
        for argv in timeline_calls:
            self.assertIn("--agent", argv)

    def test_capped_client_filtered_is_unverified(self):
        many = [{"path": f"s{i}", "agent": "opencode", "workspace": "w"}
                for i in range(6)]

        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            return {"sessions": many}

        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake_run):
            sessions, info = cass_adapter.fetch_inventory(
                since="-24h", limit=5, agent=["codex"],
                workspace=["w"])
        self.assertIsNone(info["complete"])
        inv = None
        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake_run):
            inv = collect_sessions.collect(
                None, "-24h", None, ["codex"], ["w"], 5, 30)
        self.assertTrue(
            inv["coverage"]["inventory"].startswith("UNVERIFIED"))

    def test_timeline_fallback_on_old_cass(self):
        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            if argv[0] == "timeline":
                raise cass_adapter.CassError("unexpected argument '--agent'")
            return {"sessions": [{"path": "a", "agent": "copilot_cli",
                                  "workspace": "w"}]}

        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake_run):
            sessions, info = cass_adapter.fetch_inventory(
                since="-24h", limit=10, agent=["copilot_cli"])
        self.assertIsNone(info["complete"])
        self.assertEqual(len(sessions), 1)

    def test_collect_reports_capped(self):
        many = [{"path": f"s{i}", "agent": "codex", "workspace": "w"}
                for i in range(4)]

        def fake_run(argv, timeout_s=120, machine_flag="--robot"):
            return {"sessions": many}

        with mock.patch.object(cass_adapter, "run_cass_json",
                               side_effect=fake_run):
            inv = collect_sessions.collect(
                None, "-24h", None, [], [], 3, 30)
        self.assertTrue(inv["coverage"]["limit_reached"])
        self.assertTrue(inv["coverage"]["inventory"].startswith("CAPPED"))


class CopilotEndToEndTest(unittest.TestCase):
    def test_promotion_through_real_pipeline(self):
        defs = []
        for i in range(3):
            entry, _ = copilot_session(
                sid=f"copilot-s{i}", workspace=f"proj-{i}" if i < 2 else "proj-0",
                events=[
                    {"type": "session.start", "data": {},
                     "timestamp": "2026-10-10T10:00:00.000Z"},
                    {"type": "user.message",
                     "data": {"content": "I have to do this manually every time."},
                     "timestamp": "2026-10-10T10:01:00.000Z"},
                    {"type": "assistant.message",
                     "data": {"content": "noted"},
                     "timestamp": "2026-10-10T10:01:05.000Z"},
                ])
            msgs = [
                {"type": "session.start", "data": {},
                 "timestamp": "2026-10-10T10:00:00.000Z"},
                {"type": "user.message",
                 "data": {"content": "I have to do this manually every time."},
                 "timestamp": "2026-10-10T10:01:00.000Z"},
                {"type": "assistant.message",
                 "data": {"content": "noted"},
                 "timestamp": "2026-10-10T10:01:05.000Z"},
            ]
            defs.append((entry, msgs))
        findings, norm = run_pipeline(defs)
        by_type = finding_types(findings)
        manual = by_type.get("manual_procedure", [])
        promoted = [f for f in findings["candidates"] + findings["watch_list"]
                    if f["signal_type"] == "manual_procedure"]
        self.assertTrue(promoted)
        for finding in promoted:
            self.assertIn("user_text", finding["provenance"])
        import report as report_mod
        text = report_mod.render(findings)
        self.assertIn("manually every time", text)
        for session in norm["sessions"]:
            for signal in session["signals"]:
                self.assertRegex(signal["ref"], r"#msg\d+$")

    def test_legitimate_empty_corpus_stays_valid(self):
        entry, messages = copilot_session(events=[
            {"type": "user.message",
             "data": {"content": "what time is it"},
             "timestamp": "2026-10-10T10:01:00.000Z"},
            {"type": "assistant.message",
             "data": {"content": "time to write code"},
             "timestamp": "2026-10-10T10:01:05.000Z"},
        ])
        findings, norm = run_pipeline([(entry, messages)])
        self.assertEqual(finding_types(findings), {})
        self.assertEqual(
            norm["sessions"][0]["metadata"]["content_coverage"], "full")
        self.assertEqual(norm["coverage"]["sessions_content_none"], 0)


if __name__ == "__main__":
    unittest.main()
