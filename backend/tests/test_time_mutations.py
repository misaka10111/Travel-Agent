import copy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.routes.plan import PlanRequest, _blocks_to_plan, create_plan


BLOCKS = [
    {"id": "a1", "plan_style": "经典", "day": 1, "type": "景点", "name": "上午景点", "time": "09:00-11:00"},
    {"id": "lunch", "plan_style": "经典", "day": 1, "type": "美食", "meal": "午餐", "name": "午餐店", "time": "12:00-13:00", "lng": 120.1, "lat": 30.2},
    {"id": "a2", "plan_style": "经典", "day": 1, "type": "景点", "name": "下午景点", "time": "15:00-17:00"},
    {"id": "dinner", "plan_style": "经典", "day": 1, "type": "美食", "meal": "晚餐", "name": "晚餐店", "time": "18:00-19:00", "lng": 120.5, "lat": 30.5},
    {"id": "hotel", "plan_style": "经典", "day": 1, "type": "酒店", "name": "酒店", "time": "住宿"},
]


def add_request(item, blocks=None, selected=None):
    return PlanRequest(destination="杭州", start_date="2026-10-20", end_date="2026-10-21", basic={"total_budget": 5000}, search={"destination": "杭州"}, modify={"action": "add", "plan_style": "经典", "day": 1, "blocks": copy.deepcopy(blocks if blocks is not None else BLOCKS), "item": item, "block_ids": selected or []})


def finalized(blocks, *args, **kwargs):
    return {"blocks": blocks}


class TimeMutationTests(unittest.TestCase):
    def add(self, item, blocks=None, selected=None):
        with patch("app.api.routes.plan._finalize_blocks", side_effect=finalized):
            return create_plan(add_request(item, blocks, selected))

    def test_explicit_meal_time_selects_nearest_time_instead_of_nearest_location(self):
        result = self.add({"type": "美食", "name": "新餐厅", "time": "19:00-20:00", "lng": 120.1, "lat": 30.2})
        lunch = next(item for item in result["blocks"] if item["id"] == "lunch")
        dinner = next(item for item in result["blocks"] if item["id"] == "dinner")
        self.assertEqual(lunch["name"], "午餐店")
        self.assertEqual(dinner["name"], "新餐厅")
        self.assertEqual(dinner["time"], "19:00-20:00")
        self.assertEqual(dinner["meal"], "晚餐")

    def test_explicit_selected_block_has_priority_and_route_order_follows_new_time(self):
        result = self.add({"type": "美食", "name": "新餐厅", "time": "19:00-20:00"}, selected=["lunch"])
        selected = next(item for item in result["blocks"] if item["id"] == "lunch")
        self.assertEqual(selected["name"], "新餐厅")
        self.assertEqual(selected["time"], "19:00-20:00")
        self.assertEqual(selected["meal"], "晚餐")
        self.assertEqual([item["id"] for item in result["blocks"]], ["a1", "a2", "dinner", "lunch", "hotel"])

    def test_automatic_food_replacement_preserves_old_slot_even_when_day_is_full(self):
        full = [{"id": "a1", "plan_style": "经典", "day": 1, "type": "景点", "name": "上午景点", "time": "09:00-12:00"},
                {"id": "lunch", "plan_style": "经典", "day": 1, "type": "美食", "name": "午餐店", "time": "12:00-13:00"},
                {"id": "a2", "plan_style": "经典", "day": 1, "type": "景点", "name": "下午景点", "time": "13:00-20:00"}]
        result = self.add({"type": "美食", "name": "替换餐厅"}, full)
        self.assertEqual(len(result["blocks"]), len(full))
        self.assertEqual(result["blocks"][1]["time"], "12:00-13:00")
        self.assertEqual(result["blocks"][1]["meal"], "午餐")

    def test_hotel_replacement_keeps_lodging_marker_or_applies_explicit_time(self):
        automatic = self.add({"type": "酒店", "name": "新酒店"})
        self.assertEqual(next(item for item in automatic["blocks"] if item["id"] == "hotel")["time"], "住宿")
        explicit = self.add({"type": "酒店", "name": "新酒店", "time": "19:00-20:00"})
        self.assertEqual(next(item for item in explicit["blocks"] if item["id"] == "hotel")["time"], "19:00-20:00")

    def test_automatic_activity_is_after_arrival_and_before_return(self):
        window = {"start_min": 15 * 60 + 30, "end_min": 19 * 60, "start": "15:30", "end": "19:00"}
        blocks = [{"id": "a1", "plan_style": "经典", "day": 1, "type": "景点", "name": "原景点", "time": "15:30-16:30", "activity_window": window}]
        result = self.add({"type": "景点", "name": "新景点"}, blocks)
        added = next(item for item in result["blocks"] if item["name"] == "新景点")
        self.assertEqual(added["time"], "16:30-17:30")
        self.assertEqual(added["activity_window"], window)

    def test_full_activity_window_rejects_without_falling_back_to_conflicting_evening(self):
        blocks = [{"id": "a1", "plan_style": "经典", "day": 1, "type": "景点", "name": "全天景点", "time": "09:00-15:00", "activity_window": {"start_min": 9 * 60, "end_min": 15 * 60 + 30}}]
        with patch("app.api.routes.plan._finalize_blocks") as finalize:
            with self.assertRaisesRegex(HTTPException, "换一天"):
                create_plan(add_request({"type": "景点", "name": "新景点"}, blocks))
        finalize.assert_not_called()

    def test_explicit_invalid_overlap_and_outside_window_times_are_rejected(self):
        windowed = [{**item, "activity_window": {"start_min": 9 * 60, "end_min": 20 * 60}} for item in BLOCKS]
        for value in ("23:30-24:30", "23:30-01:00", "10:00-10:30", "08:00-09:00"):
            with self.subTest(time=value):
                with self.assertRaises(HTTPException):
                    self.add({"type": "景点", "name": "新景点", "time": value}, windowed)
        with self.assertRaises(HTTPException):
            self.add({"type": "美食", "name": "新餐厅", "time": "16:00-17:00"}, windowed, ["lunch"])

    def test_rebuild_restores_current_day_window_without_resurrecting_deleted_hotel(self):
        window = {"start_min": 14 * 60, "end_min": 19 * 60}
        blocks = [{**BLOCKS[0], "activity_window": window, "date": "2026-10-20"}]
        metadata = {"plans": [{"style": "经典", "summary": "原方案", "itinerary": [{"day": 1, "hotel": "已经删除的旧酒店", "activity_window": {"start_min": 9 * 60, "end_min": 22 * 60}}]}]}
        result = _blocks_to_plan(blocks, "杭州", "2026-10-20", "2026-10-21", metadata)
        day = result["plans"][0]["itinerary"][0]
        self.assertEqual(day["activity_window"], window)
        self.assertEqual(day["hotel"], "")
        self.assertFalse(any(item["type"] == "酒店" for item in day["schedule"]))


if __name__ == "__main__":
    unittest.main()
