import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import resolve_intent
from trip_intent import complete_trip_request, normalize_trip_data


TODAY = date(2026, 10, 6)
TRIP = {
    "destination": "杭州",
    "origin": "上海",
    "start_date": "2026-10-09",
    "duration_days": 3,
    "travelers": "2人",
    "total_budget": 5000,
}


class TripIntentTests(unittest.TestCase):
    def test_complete_input_skips_questionnaire_and_optional_fields(self):
        result = complete_trip_request(TRIP, today=TODAY)
        self.assertEqual(result["action"], "plan")
        self.assertEqual(result["data"]["end_date"], "2026-10-11")
        self.assertNotIn("budget_tiers", result["data"])
        self.assertNotIn("purposes", result["data"])

    def test_followup_only_requests_missing_information(self):
        initial = {"destination": "杭州", "start_date": "2026-10-09", "duration_days": 3}
        first = complete_trip_request(initial, today=TODAY)
        self.assertEqual(first["missing"], ["origin", "travelers", "total_budget"])
        self.assertEqual(first["options"], [])
        second = complete_trip_request(
            {"origin": "上海", "travelers": "2大1小", "total_budget": "1万元"},
            previous=first["data"], today=TODAY,
        )
        self.assertEqual(second["action"], "plan")
        self.assertEqual(second["data"]["travelers"], "3人")
        self.assertEqual(second["data"]["total_budget"], 10000)
        self.assertEqual(second["data"]["destination"], "杭州")

    def test_duration_does_not_invent_departure_date_or_repeat_duration(self):
        data = {key: value for key, value in TRIP.items() if key != "start_date"}
        result = complete_trip_request(data, today=TODAY)
        self.assertEqual(result["missing"], ["start_date"])
        self.assertNotIn("start_date", result["data"])
        self.assertNotIn("end_date", result["data"])

    def test_return_date_is_not_assumed_without_date_or_duration(self):
        data = {key: value for key, value in TRIP.items() if key != "duration_days"}
        self.assertEqual(complete_trip_request(data, today=TODAY)["missing"], ["end_date"])

    def test_invalid_dates_request_correction(self):
        data = {**TRIP, "duration_days": None, "start_date": "2026-10-09", "end_date": "2026-10-08"}
        self.assertEqual(complete_trip_request(data, today=TODAY)["missing"], ["end_date"])
        data["start_date"] = "2026-10-01"
        result = complete_trip_request(data, today=TODAY)
        self.assertIn("start_date", result["missing"])

    def test_unlimited_budget_does_not_force_amount(self):
        data = {key: value for key, value in TRIP.items() if key != "total_budget"}
        result = complete_trip_request({**data, "budget_unlimited": True}, today=TODAY)
        self.assertEqual(result["action"], "plan")
        self.assertEqual(result["data"]["budget_tiers"], ["不设限"])

    def test_per_person_budget_converts_after_travelers_are_known(self):
        data = {key: value for key, value in TRIP.items() if key not in ("total_budget", "travelers")}
        first = complete_trip_request({**data, "budget_per_person": 2500}, today=TODAY)
        self.assertEqual(first["missing"], ["travelers"])
        second = complete_trip_request({"travelers": "2人"}, first["data"], TODAY)
        self.assertEqual(second["action"], "plan")
        self.assertEqual(second["data"]["total_budget"], 5000)
        updated = normalize_trip_data({"travelers": "3人"}, second["data"], TODAY)
        self.assertEqual(updated["total_budget"], 7500)

    def test_duration_correction_preserves_sights_and_preferences(self):
        old = normalize_trip_data({**TRIP, "requested_pois": ["西湖"], "notes": "不吃辣"}, today=TODAY)
        result = normalize_trip_data({"duration_days": 4}, old, TODAY)
        self.assertEqual(result["end_date"], "2026-10-12")
        self.assertEqual(result["requested_pois"], ["西湖"])
        self.assertEqual(result["notes"], "不吃辣")

    def test_invalid_budget_and_fractional_travelers_are_not_accepted(self):
        result = complete_trip_request({**TRIP, "total_budget": -10, "travelers": "2.5人"}, today=TODAY)
        self.assertEqual(result["missing"], ["travelers", "total_budget"])


class ModelContractTests(unittest.TestCase):
    def client(self, result):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))
        ])
        return client

    def test_model_plan_action_still_checks_missing_fields(self):
        result = resolve_intent(
            {"messages": [{"role": "user", "content": "我想去杭州"}]},
            self.client({"action": "plan", "data": {"destination": "杭州"}}),
        )
        self.assertEqual(result["action"], "ask")
        self.assertNotIn("destination", result["missing"])
        self.assertEqual(result["options"], [])

    def test_followup_preserves_extracted_state_and_uses_current_date(self):
        previous = {**TRIP, "start_date": (date.today() + timedelta(days=5)).isoformat()}
        client = self.client({"action": "collect", "data": {"total_budget": 6000}})
        result = resolve_intent({
            "messages": [{"role": "user", "content": "预算改成6000"}], "trip_data": previous,
        }, client)
        self.assertEqual(result["action"], "plan")
        self.assertEqual(result["data"]["total_budget"], 6000)
        self.assertEqual(result["data"]["destination"], "杭州")
        request = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn(date.today().isoformat(), request[0]["content"])
        self.assertEqual(json.loads(request[1]["content"])["trip_data"], previous)

    def test_new_trip_does_not_use_old_trip_budget_or_dates(self):
        result = resolve_intent({
            "messages": [{"role": "user", "content": "重新规划成都旅行"}],
            "has_plan": True, "trip_data": TRIP,
        }, self.client({"action": "collect", "new_trip": True, "data": {"destination": "成都"}}))
        self.assertEqual(result["data"], {"destination": "成都"})
        self.assertIn("total_budget", result["missing"])

    def test_explanation_and_modification_keep_existing_actions(self):
        for action in ("explain", "confirm", "modify"):
            output = {"action": action, "answer": "示例", "instruction": "少走路"}
            result = resolve_intent({"messages": [{"role": "user", "content": "少走路"}]}, self.client(output))
            self.assertEqual(result, output)


if __name__ == "__main__":
    unittest.main()
