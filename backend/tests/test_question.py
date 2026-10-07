import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes.question import QuestionRequest, ask_question


class QuestionRouteTests(unittest.TestCase):
    def test_followup_sends_previous_trip_data_to_agent(self):
        previous = {"destination": "杭州", "duration_days": 3}
        payload = QuestionRequest(
            messages=[{"role": "user", "content": "2人，预算5000元"}],
            trip_data=previous,
        )
        reply = {"action": "ask", "data": previous, "missing": ["start_date"], "options": []}
        with patch("app.api.routes.question.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(reply))) as run:
            result = ask_question(payload)
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["trip_data"], previous)
        self.assertEqual(request["messages"], payload.messages)
        self.assertEqual(result, reply)

    def test_dynamic_questionnaire_preserves_options_and_missing_state(self):
        payload = QuestionRequest(messages=[{"role": "user", "content": "从上海去杭州玩3天，2人，预算6000"}])
        reply = {
            "action": "ask", "data": {"destination": "杭州", "duration_days": 3},
            "missing": ["start_date"], "question": "补充出发日期", "options": [],
            "questions": [{"field": "start_date", "question": "何时出发去杭州？", "options": ["2026-10-09", "2026-10-10"]}],
        }
        with patch("app.api.routes.question.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(reply))):
            self.assertEqual(ask_question(payload), reply)

    def test_invalid_model_response_does_not_expose_process_output(self):
        payload = QuestionRequest(messages=[{"role": "user", "content": "杭州"}])
        for output in ("unexpected internal output", "[]"):
            with patch("app.api.routes.question.subprocess.run", return_value=SimpleNamespace(stdout=output, stderr="internal details")):
                reply = ask_question(payload)
            self.assertIn("error", reply)
            self.assertNotIn("internal", reply["error"])

    def test_timeout_returns_recoverable_message(self):
        payload = QuestionRequest(messages=[{"role": "user", "content": "杭州"}])
        with patch("app.api.routes.question.subprocess.run", side_effect=subprocess.TimeoutExpired("question", 75)):
            self.assertIn("超时", ask_question(payload)["error"])


if __name__ == "__main__":
    unittest.main()
