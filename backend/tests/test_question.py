import json
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


if __name__ == "__main__":
    unittest.main()
