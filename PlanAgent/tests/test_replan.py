"""Run with python -m unittest discover -s PlanAgent/tests -v (no API keys)."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replan import ReplanError, replan_plan, replacement, catalog, expected_pairs


def block(bid, day, kind, name, time, style="推荐方案"):
    return {"id": bid, "day": day, "date": f"2026-10-{day + 19:02d}", "type": kind, "name": name, "time": time, "plan_style": style, "price": 20, "lng": 120.1, "lat": 30.1}


class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.original = {"destination": "杭州", "start_date": "2026-10-20", "end_date": "2026-10-21", "revision": 0, "blocks": [block("a", 1, "景点", "灵隐寺", "09:00-11:00"), block("b", 1, "美食", "午餐店", "11:30-12:30"), block("h", 1, "酒店", "酒店甲", "18:00-18:30"), block("d", 2, "景点", "次日景点", "09:00-11:00"), block("other", 1, "景点", "另一方案", "09:00-11:00", "其他方案")], "legs": [{"plan_style": "其他方案", "day": 1, "from": "x", "to": "y", "duration_s": 10}]}
        self.payload = {"plan": self.original, "revision": 0, "plan_style": "推荐方案", "target_block_ids": ["a"], "instruction": "换个文化景点"}
        self.search = {"poi": [{"name": "博物馆", "price": 30}, {"name": "第二候选", "price": 10}], "hotels": [{"name": "酒店乙", "price": 50}], "food": [{"name": "新餐厅", "price_per_person": 40}]}

    def choose(self, context):
        return {"replacements": [{"block_id": b["id"], "candidate_id": context["candidates"][b["id"]][0]["candidate_id"]} for b in context["targets"]]}

    def routes(self, result, city):
        for i, b in enumerate(result["blocks"]):
            b["lng"], b["lat"] = 120 + i / 100, 30 + i / 100
        result["legs"] = [{"plan_style": "推荐方案", "day": day, "from": left, "to": right, "duration_s": 3600, "mode": "drive", "distance_m": 1000, "polyline": [[120, 30], [120.1, 30.1]]} for day, left, right in expected_pairs(result["blocks"], "推荐方案")]

    def run_plan(self, routes=None, choose=None):
        return replan_plan(self.payload, self.search, choose or self.choose, routes or self.routes)

    def test_replaces_target_and_repairs_downstream_time(self):
        result = self.run_plan()
        by_id = {b["id"]: b for b in result["plan"]["blocks"]}
        self.assertEqual(by_id["a"]["name"], "博物馆")
        self.assertEqual(by_id["b"]["time"], "12:10-13:10")
        self.assertEqual(by_id["a"]["id"], "a")
        self.assertEqual(result["revision"], 1)

    def test_failure_or_success_never_mutates_input(self):
        before = deepcopy(self.payload)
        self.run_plan()
        self.assertEqual(before, self.payload)
        self.payload["locked_block_ids"] = ["b"]
        before = deepcopy(self.payload)
        with self.assertRaises(ReplanError):
            self.run_plan()
        self.assertEqual(before, self.payload)

    def test_preserves_other_style_and_unaffected_day(self):
        result = self.run_plan()["plan"]
        for bid in ("other", "d"):
            self.assertEqual(next(b for b in result["blocks"] if b["id"] == bid), next(b for b in self.original["blocks"] if b["id"] == bid))
        self.assertIn(self.original["legs"][0], result["legs"])

    def test_hotel_change_recalculates_next_day_departure(self):
        self.payload["target_block_ids"] = ["h"]
        result = self.run_plan()
        self.assertEqual(result["affected_days"], [1, 2])
        day_two = next(b for b in result["plan"]["blocks"] if b["id"] == "d")
        self.assertEqual(day_two["time"], "09:10-11:10")
        self.assertTrue(any(l["day"] == 2 and l["from"] == "h" for l in result["plan"]["legs"]))

    def test_missing_routes_preserves_original(self):
        with self.assertRaisesRegex(ReplanError, "交通路线"):
            self.run_plan(routes=lambda result, city: None)

    def test_no_candidates(self):
        self.search["poi"] = []
        with self.assertRaisesRegex(ReplanError, "替代项"):
            self.run_plan()

    def test_rejects_model_invented_candidate(self):
        with self.assertRaisesRegex(ReplanError, "搜索结果"):
            self.run_plan(choose=lambda _: {"replacements": [{"block_id": "a", "candidate_id": "invented"}]})

    def test_rejects_wrong_style_and_duplicate_ids(self):
        self.payload["target_block_ids"] = ["other"]
        with self.assertRaisesRegex(ReplanError, "当前方案"):
            self.run_plan()
        self.payload["target_block_ids"] = ["a"]
        self.original["blocks"].append(deepcopy(self.original["blocks"][0]))
        with self.assertRaisesRegex(ReplanError, "唯一"):
            self.run_plan()

    def test_stale_revision(self):
        self.payload["revision"] = 1
        with self.assertRaisesRegex(ReplanError, "版本"):
            self.run_plan()

    def test_budget_overflow(self):
        self.payload["basic"] = {"total_budget": 1}
        with self.assertRaisesRegex(ReplanError, "预算"):
            self.run_plan()

    def test_unknown_candidate_price_with_budget(self):
        self.payload["basic"] = {"total_budget": 1000}
        self.search["poi"][0].pop("price")
        with self.assertRaisesRegex(ReplanError, "缺少价格"):
            self.run_plan()

    def test_free_candidate_is_zero_not_unknown(self):
        self.assertEqual(replacement(self.original["blocks"][0], {"name": "免费公园", "price": 0})["price"], 0)
        self.assertEqual(replacement(self.original["blocks"][0], {"name": "免费公园", "free": True})["price"], 0)

    def test_new_place_does_not_inherit_old_coordinates_or_options(self):
        old = {**self.original["blocks"][0], "options": [{"name": "旧地点"}], "poi_id": "old"}
        new = replacement(old, {"name": "博物馆", "price": 10})
        for field in ("lng", "lat", "poi_id", "options"):
            self.assertNotIn(field, new)

    def test_opening_hours_conflict(self):
        self.search["poi"][0]["opening_hours"] = "15:00-16:00"
        with self.assertRaisesRegex(ReplanError, "营业时间"):
            self.run_plan()

    def test_retries_with_constraint_feedback(self):
        self.search["poi"][0]["opening_hours"] = "15:00-16:00"
        calls = []
        def choose(context):
            calls.append(context)
            return {"replacements": [{"block_id": "a", "candidate_id": "c0" if len(calls) == 1 else "c1"}]}
        result = self.run_plan(choose=choose)
        self.assertEqual(len(calls), 2)
        self.assertIn("营业时间", calls[1]["feedback"])
        self.assertEqual(result["plan"]["blocks"][0]["name"], "第二候选")

    def test_multiple_targets_keep_stable_ids(self):
        self.payload["target_block_ids"] = ["a", "b"]
        result = self.run_plan()
        self.assertEqual([b["id"] for b in result["plan"]["blocks"]], [b["id"] for b in self.original["blocks"]])
        self.assertEqual(result["plan"]["blocks"][1]["name"], "新餐厅")

    def test_unselected_previous_slot_keeps_original_time(self):
        self.payload["target_block_ids"] = ["b"]
        result = self.run_plan()
        self.assertEqual(result["plan"]["blocks"][0]["time"], "09:00-11:00")

    def test_event_on_other_date_is_excluded(self):
        self.assertEqual(catalog({"events": [{"title": "演唱会", "date": "2026-10-21"}]}, block("event", 1, "活动", "展览", "14:00-16:00")), [])

    def test_weather_is_not_replannable(self):
        self.original["blocks"][0]["type"] = "天气"
        with self.assertRaisesRegex(ReplanError, "类别"):
            self.run_plan()

    def test_rebuilt_nested_schedule_matches_blocks(self):
        result = self.run_plan()["plan"]
        nested = [item for p in result["plans"] for day in p["itinerary"] for item in day["schedule"]]
        self.assertEqual(nested, result["blocks"])

    def test_outbound_replacement_uses_arrival_station_and_delays_day(self):
        flight = block("flight", 1, "交通", "A1", "08:00-09:00")
        self.original["blocks"].insert(0, flight)
        self.payload["target_block_ids"] = ["flight"]
        self.search["flights"] = [
            {"airline": "A", "flight_no": "1", "dep_time": "2026-10-20 08:00", "arr_time": "2026-10-20 09:00", "arr_station": "杭州机场", "dep_station": "上海机场", "direction": "去", "price": 100},
            {"airline": "B", "flight_no": "2", "dep_time": "2026-10-20 11:00", "arr_time": "2026-10-20 12:00", "arr_station": "杭州机场", "dep_station": "上海机场", "direction": "去", "price": 100},
        ]
        result = self.run_plan()["plan"]
        self.assertEqual(result["blocks"][0]["name"], "B2")
        self.assertEqual(result["blocks"][0]["type"], "交通")
        self.assertEqual(result["blocks"][1]["time"], "14:10-16:10")
        self.assertTrue(any(l["from"] == "flight" for l in result["legs"]))

    def test_return_trip_needs_real_transfer_and_buffer(self):
        self.original["blocks"] = [b for b in self.original["blocks"] if b["id"] != "h"]
        self.original["blocks"].insert(2, block("flight", 1, "交通", "A1", "18:00-19:00"))
        self.payload["target_block_ids"] = ["flight"]
        self.search["flights"] = [
            {"airline": "A", "flight_no": "1", "dep_time": "2026-10-20 18:00", "arr_time": "2026-10-20 19:00", "arr_station": "上海机场", "dep_station": "杭州机场", "direction": "返", "price": 100},
            {"airline": "B", "flight_no": "2", "dep_time": "2026-10-20 14:00", "arr_time": "2026-10-20 15:00", "arr_station": "上海机场", "dep_station": "杭州机场", "direction": "返", "price": 100},
        ]
        with self.assertRaisesRegex(ReplanError, "固定出行时间"):
            self.run_plan()

    def test_continuous_hotel_stays_are_replaced_together(self):
        self.original["blocks"].insert(4, block("h2", 2, "酒店", "酒店甲", "18:00-18:30"))
        self.payload["target_block_ids"] = ["h"]
        result = self.run_plan()["plan"]
        self.assertEqual([b["name"] for b in result["blocks"] if b["type"] == "酒店"], ["酒店乙", "酒店乙"])

    def test_late_day_overflow_fails(self):
        self.original["blocks"][0]["time"] = "22:00-23:00"
        with self.assertRaisesRegex(ReplanError, "23:30"):
            self.run_plan()

    def test_no_stay_label_does_not_require_geocoding(self):
        self.original["blocks"][2]["name"] = "当晚返程，无住宿"
        result = self.run_plan()
        self.assertEqual(result["plan"]["blocks"][2], self.original["blocks"][2])


if __name__ == "__main__":
    unittest.main()
