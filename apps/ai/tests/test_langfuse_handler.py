import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


class LangfuseHandlerDisabledTests(unittest.TestCase):
    """When credentials are absent, all calls should be silent no-ops."""

    def setUp(self):
        import handlers.langfuse_handler as lh
        lh._langfuse_client = None

    def _clear_env(self):
        for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
            os.environ.pop(key, None)

    def test_get_client_returns_none_when_no_keys(self):
        self._clear_env()
        from handlers.langfuse_handler import get_langfuse_client
        self.assertIsNone(get_langfuse_client())

    def test_create_call_trace_returns_none_when_disabled(self):
        self._clear_env()
        from handlers.langfuse_handler import create_call_trace
        result = create_call_trace(
            call_id="c1", agent_id="a1", organization_id="o1",
            system_prompt="Be helpful", call_context={},
            started_at=datetime.now(timezone.utc),
        )
        self.assertIsNone(result)

    def test_record_turn_is_noop_when_trace_is_none(self):
        from handlers.langfuse_handler import record_turn
        record_turn(None, role="user", content="hi",
                    started_at=datetime.now(timezone.utc),
                    ended_at=datetime.now(timezone.utc))

    def test_submit_scores_is_noop_when_trace_is_none(self):
        from handlers.langfuse_handler import submit_scores
        submit_scores(None, [{"identifier": "q", "value": True}])

    def test_flush_langfuse_is_noop_when_no_client(self):
        self._clear_env()
        from handlers.langfuse_handler import flush_langfuse
        flush_langfuse()


class LangfuseHandlerEnabledTests(unittest.TestCase):
    """When credentials are present, calls should delegate to the SDK."""

    def setUp(self):
        import handlers.langfuse_handler as lh
        lh._langfuse_client = None

    def _set_env(self):
        os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-test"
        os.environ["LANGFUSE_SECRET_KEY"] = "sk-test"
        os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"

    def tearDown(self):
        for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
            os.environ.pop(key, None)
        import handlers.langfuse_handler as lh
        lh._langfuse_client = None

    def _make_mock_client(self):
        mock_trace = MagicMock()
        mock_client = MagicMock()
        mock_client.trace.return_value = mock_trace
        return mock_client, mock_trace

    def test_create_call_trace_calls_sdk_trace(self):
        self._set_env()
        import handlers.langfuse_handler as lh
        mock_client, mock_trace = self._make_mock_client()
        lh._langfuse_client = mock_client
        from handlers.langfuse_handler import create_call_trace
        now = datetime.now(timezone.utc)
        result = create_call_trace(
            call_id="call_abc", agent_id="agent_1", organization_id="org_1",
            system_prompt="Hello", call_context={"direction": "inbound"},
            started_at=now,
        )
        mock_client.trace.assert_called_once()
        call_kwargs = mock_client.trace.call_args.kwargs
        self.assertEqual(call_kwargs["id"], "call_abc")
        self.assertEqual(call_kwargs["user_id"], "org_1")
        self.assertIn("agent:agent_1", call_kwargs["tags"])
        self.assertEqual(result, mock_trace)

    def test_record_turn_calls_span_on_trace(self):
        mock_trace = MagicMock()
        from handlers.langfuse_handler import record_turn
        now = datetime.now(timezone.utc)
        record_turn(mock_trace, role="agent", content="Hi there",
                    started_at=now, ended_at=now)
        mock_trace.span.assert_called_once()
        self.assertEqual(mock_trace.span.call_args.kwargs["name"], "agent-turn")

    def test_submit_scores_maps_bool_to_numeric(self):
        mock_trace = MagicMock()
        from handlers.langfuse_handler import submit_scores
        submit_scores(mock_trace, [
            {"identifier": "qualified", "value": True},
            {"identifier": "declined", "value": False},
        ])
        calls = mock_trace.score.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["value"], 1.0)
        self.assertEqual(calls[1].kwargs["value"], 0.0)

    def test_submit_scores_maps_yes_no_strings(self):
        mock_trace = MagicMock()
        from handlers.langfuse_handler import submit_scores
        submit_scores(mock_trace, [
            {"identifier": "a", "value": "yes"},
            {"identifier": "b", "value": "no"},
        ])
        values = [c.kwargs["value"] for c in mock_trace.score.call_args_list]
        self.assertEqual(values, [1.0, 0.0])

    def test_submit_scores_stores_non_numeric_string_as_comment(self):
        mock_trace = MagicMock()
        from handlers.langfuse_handler import submit_scores
        submit_scores(mock_trace, [{"identifier": "sentiment", "value": "positive"}])
        kwargs = mock_trace.score.call_args.kwargs
        self.assertEqual(kwargs["value"], 0.0)
        self.assertEqual(kwargs["comment"], "positive")

    def test_submit_scores_attaches_extracted_data_to_trace_output(self):
        mock_trace = MagicMock()
        from handlers.langfuse_handler import submit_scores
        submit_scores(mock_trace, [], extracted_data=[{"name": "name", "value": "Alice"}])
        mock_trace.update.assert_called_once()
        out = mock_trace.update.call_args.kwargs["output"]
        self.assertIn("extracted_data", out)

    def test_flush_calls_sdk_flush(self):
        self._set_env()
        import handlers.langfuse_handler as lh
        mock_client = MagicMock()
        lh._langfuse_client = mock_client
        from handlers.langfuse_handler import flush_langfuse
        flush_langfuse()
        mock_client.flush.assert_called_once()

    def test_phone_numbers_excluded_from_trace_metadata(self):
        self._set_env()
        import handlers.langfuse_handler as lh
        mock_client, _ = self._make_mock_client()
        lh._langfuse_client = mock_client
        from handlers.langfuse_handler import create_call_trace
        create_call_trace(
            call_id="c", agent_id="a", organization_id="o",
            system_prompt="p",
            call_context={
                "direction": "inbound",
                "from_number": "+15550001111",
                "to_number": "+15551230000",
            },
            started_at=datetime.now(timezone.utc),
        )
        meta = mock_client.trace.call_args.kwargs["metadata"]
        self.assertNotIn("from_number", meta)
        self.assertNotIn("to_number", meta)
        self.assertIn("direction", meta)


if __name__ == "__main__":
    unittest.main()
