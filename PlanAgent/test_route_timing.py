"""真实转场时间必须容纳在行程中；局部修改不能悄悄改未选条目的时间。"""
import copy
import unittest

import plan


class RouteTimingTests(unittest.TestCase):
    def sample(self):
        return {"blocks": [
            {"id": "meal", "type": "美食", "meal": "午餐", "day": 1, "plan_style": "经典", "name": "餐厅", "time": "12:00-13:00"},
            {"id": "spot", "type": "景点", "day": 1, "plan_style": "经典", "name": "灵隐寺", "time": "13:00-16:00", "activity_window": {"end_min": 1200}},
            {"id": "dinner", "type": "美食", "meal": "晚餐", "day": 1, "plan_style": "经典", "name": "晚餐厅", "time": "18:00-19:00"}],
            "legs": [{"from": "meal", "to": "spot", "duration_s": 3791},
                     {"from": "spot", "to": "dinner", "duration_s": 583}]}

    def test_initial_plan_reserves_actual_sixty_three_minute_transfer(self):
        sample = self.sample()
        plan._align_route_times(sample, adjust=True)
        self.assertEqual(sample["blocks"][1]["time"], "14:09-17:09")
        self.assertEqual(sample["blocks"][2]["time"], "18:00-19:00")
        self.assertEqual(sample["travel_time_warnings"], [])

    def test_shift_propagates_and_updates_candidate_meal_time(self):
        sample = self.sample()
        sample["blocks"][1]["time"] = "13:00-17:00"
        sample["food_by_anchor"] = [{"plan_style": "经典", "day": 1, "meal": "晚餐", "time": "18:00-19:00"}]
        plan._align_route_times(sample, adjust=True)
        self.assertEqual(sample["blocks"][2]["time"], "18:24-19:24")
        self.assertEqual(sample["food_by_anchor"][0]["time"], "18:24-19:24")

    def test_modification_keeps_user_and_unselected_times_with_clear_warning(self):
        sample = self.sample()
        original = copy.deepcopy(sample["blocks"])
        plan._align_route_times(sample)
        self.assertEqual(sample["blocks"], original)
        self.assertEqual(len(sample["travel_time_warnings"]), 1)
        self.assertIn("69分钟", sample["travel_time_warnings"][0])

    def test_shift_cannot_cross_return_activity_window(self):
        sample = self.sample()
        sample["blocks"][1]["activity_window"]["end_min"] = 17 * 60
        plan._align_route_times(sample, adjust=True)
        self.assertEqual(sample["blocks"][1]["time"], "13:00-16:00")
        self.assertTrue(sample["travel_time_warnings"])

    def test_existing_sufficient_gap_is_unchanged_and_warnings_clear_after_fix(self):
        sample = self.sample()
        plan._align_route_times(sample)
        sample["blocks"][1]["time"] = "14:10-17:10"
        plan._align_route_times(sample)
        self.assertEqual(sample["travel_time_warnings"], [])
        self.assertEqual(sample["warnings"], [])


if __name__ == "__main__":
    unittest.main()
