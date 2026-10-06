"""选餐不能沿用前一家餐厅的路程或未知价格标记。"""
import copy
import unittest
from unittest.mock import patch

from app.api.routes.plan import PlanRequest, create_plan


class MealMetadataTests(unittest.TestCase):
    def mutate(self, action, item):
        old = {"id": "m1", "plan_style": "经典", "day": 1, "type": "美食", "name": "旧餐厅", "time": "12:00-13:00",
            "meal": "午餐", "lng": 120.15, "lat": 30.27, "price_source": "unknown", "price_known": False,
            "walking_distance_m": 100, "walking_duration_s": 90, "walking_origin": "120.140000,30.270000"}
        payload = PlanRequest(destination="杭州", start_date="2026-10-20", end_date="2026-10-20",
            modify={"action": action, "blocks": [copy.deepcopy(old)], "block_ids": ["m1"], "plan_style": "经典", "day": 1, "item": item})
        def finalize(blocks, *args, **kwargs):
            return {"blocks": blocks}
        with patch("app.api.routes.plan._finalize_blocks", side_effect=finalize):
            return create_plan(payload)["blocks"][0]

    def test_update_retains_new_walking_fields_and_clears_unknown_price_marker(self):
        item = {"name": "新餐厅", "type": "美食", "price": 60, "lng": 120.152, "lat": 30.27,
            "walking_distance_m": 750, "walking_duration_s": 600, "walking_origin": "120.140000,30.270000"}
        result = self.mutate("update", item)
        self.assertEqual(result["walking_distance_m"], 750)
        self.assertEqual(result["walking_duration_s"], 600)
        self.assertNotIn("price_source", result)
        self.assertEqual(result["price"], 60)

    def test_add_replacement_keeps_new_walking_data_in_actual_option(self):
        item = {"name": "新餐厅", "type": "美食", "price": 60, "lng": 120.152, "lat": 30.27,
            "walking_distance_m": 750, "walking_duration_s": 600, "walking_origin": "120.140000,30.270000"}
        result = self.mutate("add", item)
        self.assertEqual(result["id"], "m1")
        self.assertEqual(result["walking_distance_m"], 750)
        self.assertEqual(result["options"][-1]["walking_duration_s"], 600)
        self.assertNotIn("price_source", result)

    def test_rename_without_new_walk_clears_previous_walk(self):
        for action in ("update", "add"):
            with self.subTest(action=action):
                result = self.mutate(action, {"name": "新餐厅", "type": "美食", "price": 60, "lng": 120.152, "lat": 30.27})
                self.assertNotIn("walking_distance_m", result)
                self.assertNotIn("walking_origin", result)

    def test_same_name_new_coordinates_clears_previous_walk(self):
        result = self.mutate("update", {"name": "旧餐厅", "lng": 120.152, "lat": 30.27})
        self.assertNotIn("walking_distance_m", result)
        self.assertNotIn("walking_duration_s", result)


if __name__ == "__main__":
    unittest.main()
