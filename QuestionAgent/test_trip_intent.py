import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import resolve_intent
from trip_intent import complete_trip_request, local_today, normalize_trip_data


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
    def test_complete_input_confirms_trip_and_skips_optional_fields(self):
        result = complete_trip_request(TRIP, today=TODAY)
        self.assertEqual(result["action"], "confirm_trip")
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
        self.assertEqual(second["action"], "confirm_trip")
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
        self.assertEqual(result["action"], "confirm_trip")
        self.assertEqual(result["data"]["budget_tiers"], ["不设限"])

    def test_per_person_budget_converts_after_travelers_are_known(self):
        data = {key: value for key, value in TRIP.items() if key not in ("total_budget", "travelers")}
        first = complete_trip_request({**data, "budget_per_person": 2500}, today=TODAY)
        self.assertEqual(first["missing"], ["travelers"])
        second = complete_trip_request({"travelers": "2人"}, first["data"], TODAY)
        self.assertEqual(second["action"], "confirm_trip")
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

    def test_switching_to_total_budget_does_not_recalculate_after_traveler_change(self):
        previous = normalize_trip_data({**TRIP, "total_budget": None, "budget_per_person": 2500}, today=TODAY)
        self.assertEqual(previous["total_budget"], 5000)
        total = normalize_trip_data({"total_budget": 6000}, previous, TODAY)
        updated = normalize_trip_data({"travelers": "3人"}, total, TODAY)
        self.assertEqual(updated["total_budget"], 6000)
        self.assertNotIn("budget_per_person", updated)
        self.assertEqual(updated["budget_mode"], "total")

    def test_switching_to_per_person_budget_or_unlimited_clears_old_mode(self):
        previous = normalize_trip_data(TRIP, today=TODAY)
        per_person = normalize_trip_data({"budget_per_person": 3000}, previous, TODAY)
        self.assertEqual(per_person["total_budget"], 6000)
        unlimited = normalize_trip_data({"budget_unlimited": True}, per_person, TODAY)
        self.assertNotIn("total_budget", unlimited)
        self.assertNotIn("budget_per_person", unlimited)
        total = normalize_trip_data({"total_budget": 4500}, unlimited, TODAY)
        self.assertEqual(total["total_budget"], 4500)
        self.assertNotIn("budget_unlimited", total)

    def test_moving_departure_preserves_three_day_duration(self):
        previous = normalize_trip_data(TRIP, today=TODAY)
        updated = normalize_trip_data({"start_date": "2026-10-08"}, previous, TODAY)
        self.assertEqual(updated["duration_days"], 3)
        self.assertEqual(updated["end_date"], "2026-10-10")

    def test_editing_explicit_return_updates_duration_for_future_moves(self):
        previous = normalize_trip_data(TRIP, today=TODAY)
        extended = normalize_trip_data({"end_date": "2026-10-13"}, previous, TODAY)
        self.assertEqual(extended["duration_days"], 5)
        updated = normalize_trip_data({"start_date": "2026-10-08"}, extended, TODAY)
        self.assertEqual(updated["end_date"], "2026-10-12")

    def test_clearing_fields_reasks_without_reusing_old_values(self):
        previous = normalize_trip_data(TRIP, today=TODAY)
        result = complete_trip_request({"start_date": None, "total_budget": "不确定"}, previous, TODAY)
        self.assertEqual(result["missing"], ["start_date", "total_budget"])
        self.assertNotIn("end_date", result["data"])
        self.assertEqual(result["data"]["duration_days"], 3)
        cleared_duration = complete_trip_request({"duration_days": None}, previous, TODAY)
        self.assertEqual(cleared_duration["missing"], ["end_date"])
        self.assertNotIn("end_date", cleared_duration["data"])

    def test_clearing_calendar_dates_preserves_explicit_trip_length(self):
        previous = normalize_trip_data(TRIP, today=TODAY)
        result = complete_trip_request({"start_date": None, "end_date": None}, previous, TODAY)
        self.assertEqual(result["missing"], ["start_date"])
        self.assertEqual(result["data"]["duration_days"], 3)

    def test_questionnaire_only_asks_missing_fields_and_limits_one_round(self):
        suggestions = [
            {"field": "destination", "question": "去杭州吗？", "options": ["杭州"]},
            {"field": "purposes", "question": "喜欢什么？", "options": ["美食"]},
            {"field": "start_date", "question": "何时去杭州？", "options": ["2026-10-09", "2026-10-09", 3, ""]},
        ]
        result = complete_trip_request({"destination": "杭州"}, today=TODAY, questions=suggestions)
        self.assertLessEqual(len(result["questions"]), 3)
        self.assertEqual([item["field"] for item in result["questions"]], ["start_date", "end_date", "origin"])
        self.assertEqual(result["questions"][0]["options"], ["2026-10-09"])
        self.assertNotIn("start_date", result["data"])

    def test_malformed_questionnaire_uses_required_field_fallbacks(self):
        result = complete_trip_request({"destination": "杭州", "duration_days": 3}, today=TODAY, questions=[None, {"field": "start_date", "question": None}, {"field": []}])
        self.assertNotIn("end_date", result["missing"])
        self.assertEqual(result["questions"][0]["field"], "start_date")
        self.assertIn("杭州", result["questions"][0]["question"])


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
        previous = {**TRIP, "start_date": (local_today() + timedelta(days=5)).isoformat()}
        client = self.client({"action": "collect", "data": {"total_budget": 6000}})
        result = resolve_intent({
            "messages": [{"role": "user", "content": "预算改成6000"}], "trip_data": previous,
        }, client)
        self.assertEqual(result["action"], "confirm_trip")
        self.assertEqual(result["data"]["total_budget"], 6000)
        self.assertEqual(result["data"]["destination"], "杭州")
        request = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn(local_today().isoformat(), request[0]["content"])
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

    def test_model_questionnaire_suggestions_are_filtered_against_normalized_state(self):
        previous = {**TRIP, "start_date": (local_today() + timedelta(days=5)).isoformat()}
        client = self.client({
            "action": "collect", "data": {"start_date": None},
            "questions": [{"field": "start_date", "question": "新的出发日是哪天？", "options": ["2027-01-01"]}, {"field": "total_budget", "question": "预算？", "options": ["3000"]}],
        })
        result = resolve_intent({"messages": [{"role": "user", "content": "出发时间还没定"}], "trip_data": previous}, client)
        self.assertEqual(result["missing"], ["start_date"])
        self.assertEqual(result["questions"], [{"field": "start_date", "question": "新的出发日是哪天？", "options": ["2027-01-01"]}])


if __name__ == "__main__":
    unittest.main()
