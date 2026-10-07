"""餐饮规划行为测试；模型/地图调用均用 mock，避免依赖真实 API。"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan


def restaurant(name, lng=120.151, price=80, rating=4.5, lat=30.27):
    return {
        "name": name, "longitude": lng, "latitude": lat,
        "price_per_person": price, "rating": rating,
        "map_url": f"https://uri.amap.com/marker?position={lng},{lat}",
        "poi_detail_url": f"https://www.amap.com/place/{name}",
    }


class DiningPlanTests(unittest.TestCase):
    def setUp(self):
        walking = patch("plan.walking_route", return_value=None)
        walking.start()
        self.addCleanup(walking.stop)

    def test_distance_wins_without_price_or_district_hard_filters(self):
        nearby = restaurant("近邻", price=150)
        nearby["address"] = "另一个片区"
        distant = restaurant("远处", lng=120.19, price=300, rating=5)
        distant["address"] = "指定片区"
        options = plan._pick_restaurants(
            [distant, nearby], "指定片区", set(), [120.15, 30.27], "舒适",
        )
        self.assertEqual(options[0]["name"], "近邻")
        self.assertGreater(options[0]["score"], options[1]["score"])

    def test_missing_anchor_invalid_coordinates_and_far_results_are_skipped(self):
        data = [restaurant("太远", lng=121), restaurant("坏坐标", lng="NaN"),
                {"name": "无坐标", "rating": 5}]
        self.assertEqual(plan._pick_restaurants(data, "", set(), [120.15, 30.27], None), [])
        self.assertEqual(plan._pick_restaurants([restaurant("附近")], "", set(), None, None), [])

    def test_budget_uses_total_actual_people_and_days(self):
        self.assertEqual(plan._meal_budget_for_plan({"days": 3}, {
            "total_budget": 3600, "travelers": "2人",
        }), 50)
        options = plan._pick_restaurants([
            restaurant("贵的", price=500), restaurant("预算内", price=40),
        ], "", set(), [120.15, 30.27], "豪华", meal_budget=50)
        self.assertEqual(options[0]["name"], "预算内")
        self.assertFalse(options[0]["over_meal_budget"])

    def test_meals_anchor_on_actual_adjacent_spots_and_full_day_does_not_overlap(self):
        schedule = [
            {"type": "景点", "name": "上午景点", "time": "09:00-11:00"},
            {"type": "景点", "name": "傍晚景点", "time": "16:00-18:00"},
        ]
        coords = {"上午景点": [120.15, 30.27], "傍晚景点": [120.18, 30.28]}
        lunch_time = plan._slot_time(schedule, "午餐")
        dinner_time = plan._slot_time(schedule, "晚餐")
        self.assertEqual(plan._meal_anchor(schedule, lunch_time, coords)["anchor_name"], "上午景点")
        self.assertEqual(plan._meal_anchor(schedule, dinner_time, coords)["anchor_name"], "傍晚景点")
        all_day = [{"type": "景点", "name": "全天景点", "time": "09:00-17:00"}]
        self.assertEqual(plan._slot_time(all_day, "午餐"), "")
        self.assertTrue(plan._slot_time(all_day, "晚餐"))

    def test_food_is_not_added_before_arrival_or_after_return_departure(self):
        self.assertEqual(plan._slot_time([], "午餐", {"start_min": 900, "end_min": 1200}), "")
        self.assertEqual(plan._slot_time([], "晚餐", {"start_min": 540, "end_min": 990}), "")
        self.assertEqual(plan._slot_time([], "晚餐", {"start_min": 540, "end_min": 1095}), "17:15-18:15")

    def test_refresh_populates_actual_name_coordinates_links_and_candidates(self):
        sample = {"destination": "杭州", "days": 1, "plans": [{"style": "推荐方案", "itinerary": [{
            "day": 1, "date": "2026-10-10", "schedule": [
                {"type": "景点", "name": "景点A", "time": "09:00-11:00", "lng": 120.15, "lat": 30.27},
                {"type": "景点", "name": "景点B", "time": "15:00-17:00", "lng": 120.18, "lat": 30.28},
            ],
        }]}]}
        def nearby(_args, **kwargs):
            payload = json.loads(kwargs["input"])
            anchors = payload["anchors"]
            self.assertEqual([a["anchor_name"] for a in anchors], ["景点A", "景点B"])
            entries = [{**anchor, "restaurants": [restaurant(
                f"{anchor['meal']}餐厅", lng=anchor["longitude"] + 0.001,
                lat=anchor["latitude"],
            )]} for anchor in anchors]
            return SimpleNamespace(stdout=json.dumps({"food_by_anchor": entries}), returncode=0)
        with patch("plan.subprocess.run", side_effect=nearby):
            result = plan.refresh_food_for_plan(sample, {}, {"total_budget": 1000})
        meals = [item for item in result["plans"][0]["itinerary"][0]["schedule"] if item["type"] == "美食"]
        self.assertEqual(len(meals), 2)
        self.assertEqual(meals[0]["name"], "午餐餐厅")
        for meal in meals:
            self.assertTrue(meal["link"])
            self.assertIsInstance(meal["lng"], float)
            self.assertIsInstance(meal["distance_m"], int)
            self.assertEqual(meal["selected_option"], meal["name"])
            self.assertTrue(meal["options"][0]["rating"])
        self.assertEqual(len(result["food"]), 2)

    def test_targeted_refresh_preserves_other_day_and_manually_chosen_meal(self):
        old_meal = {"type": "美食", "name": "用户指定", "meal": "午餐", "time": "12:00-13:00", "user_selected": True}
        untouched = {"day": 2, "schedule": [{"type": "美食", "name": "原餐厅", "meal": "午餐", "time": "12:00-13:00"}]}
        original_day = copy.deepcopy(untouched)
        sample = {"destination": "杭州", "days": 2, "plans": [{"style": "推荐方案", "itinerary": [
            {"day": 1, "schedule": [old_meal]}, untouched,
        ]}], "food": [{"name": "原候选", "day": 2, "plan_style": "推荐方案"}]}
        with patch("plan.subprocess.run") as nearby:
            result = plan.refresh_food_for_plan(sample, {}, targets=[{"plan_style": "推荐方案", "day": 1}])
        nearby.assert_not_called()
        self.assertEqual(result["plans"][0]["itinerary"][1], original_day)
        self.assertEqual(result["plans"][0]["itinerary"][0]["schedule"], [old_meal])
        self.assertEqual(result["food"][0]["name"], "原候选")

    def test_build_plan_queries_food_after_final_attraction_schedule(self):
        search = {"destination": "杭州", "start_date": "2026-10-10", "end_date": "2026-10-10", "poi": [
            {"name": "初始景点", "longitude": 120.15, "latitude": 30.27},
            {"name": "最终景点", "longitude": 120.18, "latitude": 30.28},
        ], "food": [restaurant("旧全城餐厅", lng=121)]}
        day = {"day": 1, "date": "2026-10-10", "schedule": [
            {"type": "景点", "name": "初始景点", "time": "09:00-11:00"},
        ]}
        def finalize(result, _destination):
            result["plans"][0]["itinerary"][0]["schedule"][0]["name"] = "最终景点"
            return result
        def nearby(_args, **kwargs):
            payload = json.loads(kwargs["input"])
            self.assertTrue(all(anchor["anchor_name"] == "最终景点" for anchor in payload["anchors"]))
            self.assertTrue(all(anchor["longitude"] == 120.18 for anchor in payload["anchors"]))
            entries = [{**anchor, "restaurants": [restaurant("附近餐厅", lng=120.181, lat=30.28)]}
                       for anchor in payload["anchors"]]
            return SimpleNamespace(stdout=json.dumps({"food_by_anchor": entries}), returncode=0)
        with patch("plan.OpenAI"), patch("plan.geocode", return_value={"location": "120.15,30.27"}), \
             patch("plan._build_day_plan", return_value=day), \
             patch("plan._reorder_by_proximity", side_effect=finalize), \
             patch("plan.subprocess.run", side_effect=nearby):
            result = plan.build_plan(search, basic={"total_budget": 1000})
        self.assertTrue(result["food"])
        self.assertTrue(all(item["name"] == "附近餐厅" for item in result["food"]))


if __name__ == "__main__":
    unittest.main()
