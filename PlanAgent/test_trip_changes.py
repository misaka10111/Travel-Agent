import copy
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from trip_changes import resolve_trip_changes


TODAY = date(2026, 10, 6)
BASIC = {"travelers": "2人", "total_budget": 6000, "budget_mode": "total", "notes": "不吃辣"}


class TripChangeTests(unittest.TestCase):
    def resolve(self, instruction, **kwargs):
        return resolve_trip_changes(instruction, kwargs.get("basic", BASIC),
                                    kwargs.get("start", "2026-10-10"), kwargs.get("end", "2026-10-12"), TODAY)

    def test_total_budget_edit_keeps_people_and_date_and_clears_old_per_person_mode(self):
        result = self.resolve("总预算降到3000元", basic={**BASIC, "budget_per_person": 3000, "budget_mode": "per_person"})
        self.assertEqual(result["basic"]["total_budget"], 3000)
        self.assertEqual(result["basic"]["travelers"], "2人")
        self.assertEqual(result["basic"]["notes"], "不吃辣")
        self.assertNotIn("budget_per_person", result["basic"])
        self.assertEqual(result["start_date"], "2026-10-10")
        self.assertEqual(result["updates"], {"budget": True})

    def test_plain_budget_and_chinese_amount_are_supported(self):
        self.assertEqual(self.resolve("预算改为三千元")["basic"]["total_budget"], 3000)
        self.assertEqual(self.resolve("预算提高到1.2万元")["basic"]["total_budget"], 12000)
        self.assertEqual(self.resolve("总预算改为三千五")["basic"]["total_budget"], 3500)
        self.assertEqual(self.resolve("总预算改为两千零五元")["basic"]["total_budget"], 2005)
        self.assertEqual(self.resolve("预算从6000降到3000元")["basic"]["total_budget"], 3000)

    def test_accommodation_budget_is_not_mistaken_for_total_trip_budget(self):
        result = self.resolve("酒店预算降到400元，行程轻松一点")
        self.assertEqual(result["basic"]["total_budget"], 6000)
        self.assertEqual(result["updates"], {})

    def test_per_person_and_explicit_people_change_recalculate_total(self):
        result = self.resolve("每人预算1500元，出行人数改为3人")
        self.assertEqual(result["basic"]["travelers"], "3人")
        self.assertEqual(result["basic"]["budget_per_person"], 1500)
        self.assertEqual(result["basic"]["total_budget"], 4500)
        self.assertEqual(self.resolve("预算改为人均1500元")["basic"]["total_budget"], 3000)

    def test_unlimited_budget_drops_previous_amount(self):
        result = self.resolve("总预算不限")
        self.assertTrue(result["basic"]["budget_unlimited"])
        self.assertNotIn("total_budget", result["basic"])

    def test_moving_departure_preserves_existing_duration(self):
        result = self.resolve("改为10月20日出发")
        self.assertEqual((result["start_date"], result["end_date"], result["days"]), ("2026-10-20", "2026-10-22", 3))

    def test_relative_days_and_weeks_use_shanghai_today(self):
        for expression, expected in (("明天", "2026-10-07"), ("后天", "2026-10-08"), ("本周五", "2026-10-09"), ("这周末", "2026-10-10"), ("周末", "2026-10-10"), ("下周一", "2026-10-12"), ("下周末", "2026-10-17"), ("下下周二", "2026-10-20")):
            with self.subTest(expression=expression):
                result = self.resolve(f"改为{expression}出发")
                self.assertEqual(result["start_date"], expected)
                self.assertEqual(result["days"], 3)
        self.assertEqual(self.resolve("返程改为下周五")["end_date"], "2026-10-16")

    def test_chinese_month_and_day_are_supported(self):
        self.assertEqual(self.resolve("改为十月二十日出发")["start_date"], "2026-10-20")
        self.assertEqual(self.resolve("出发改为十一月一日")["start_date"], "2026-11-01")

    def test_unspecified_date_edit_reasks_instead_of_reporting_success(self):
        for instruction in ("出发日期改到下个月", "日期换成春节", "日期改一下"):
            with self.subTest(instruction=instruction):
                with self.assertRaisesRegex(ValueError, "补充具体"):
                    self.resolve(instruction)
        self.assertEqual(self.resolve("日期不要改，行程轻松一些")["start_date"], "2026-10-10")

    def test_extending_and_shortening_use_original_trip_length(self):
        self.assertEqual(self.resolve("多玩一天")["end_date"], "2026-10-13")
        self.assertEqual(self.resolve("少玩一天")["end_date"], "2026-10-11")
        self.assertEqual(self.resolve("改成5天")["end_date"], "2026-10-14")

    def test_return_day_edit_and_combined_date_duration_are_consistent(self):
        self.assertEqual(self.resolve("返程改为10月15日")["days"], 6)
        result = self.resolve("10月20日出发，10月23日返程")
        self.assertEqual((result["start_date"], result["end_date"], result["days"]), ("2026-10-20", "2026-10-23", 4))

    def test_invalid_edits_fail_instead_of_silently_reusing_old_values(self):
        for text in ("总预算改为0元", "总预算改为-10元", "出发日期改为10月32日", "改成0天", "少玩三天", "10月20日出发，10月19日返程", "10月20日出发，10月23日返程，玩3天"):
            with self.subTest(instruction=text):
                with self.assertRaises(ValueError):
                    self.resolve(text)


class GlobalPlannerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"destination": "杭州", "start_date": "2026-10-10", "end_date": "2026-10-12", "days": 3, "basic": BASIC, "plans": [{"style": "经典", "summary": "文化路线", "itinerary": [{"day": day, "date": f"2026-10-{9 + day:02d}", "schedule": [{"id": f"s{day}", "type": "景点", "time": "09:00-11:00", "name": f"景点{day}"}]} for day in (1, 2, 3)]}]}

    def modify(self, instruction, reply=None, trip_context=None):
        import plan
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reply or self.plan)))])
        modification = {"mode": "global", "instruction": instruction}
        if trip_context:
            modification["_trip_context"] = trip_context
        with patch("plan.OpenAI"), patch("plan.resolve_trip_changes", side_effect=lambda instruction, basic, start, end: resolve_trip_changes(instruction, basic, start, end, TODAY)), patch("plan._chat_completion", return_value=response) as model:
            result = plan.modify_plan(copy.deepcopy(self.plan), modification, {"destination": "杭州"}, basic=BASIC)
        self.last_prompt = json.loads(model.call_args.kwargs["messages"][1]["content"])
        return result

    def test_budget_is_in_prompt_and_model_cannot_change_unrequested_people_or_dates(self):
        reply = {**self.plan, "start_date": "2099-01-01", "end_date": "2099-01-10", "days": 10, "basic": {"travelers": "99人", "total_budget": 1}}
        result = self.modify("总预算降到3000元", reply)
        self.assertEqual(self.last_prompt["basic"]["total_budget"], 3000)
        self.assertEqual(result["basic"]["total_budget"], 3000)
        self.assertEqual(result["basic"]["travelers"], "2人")
        self.assertEqual(result["start_date"], self.plan["start_date"])
        self.assertEqual(result["days"], 3)

    def test_moved_departure_rewrites_every_schedule_day_date(self):
        result = self.modify("改为10月20日出发")
        expected = ["2026-10-20", "2026-10-21", "2026-10-22"]
        self.assertEqual(result["end_date"], expected[-1])
        self.assertEqual([day["date"] for day in result["plans"][0]["itinerary"]], expected)
        self.assertEqual([day["schedule"][0]["date"] for day in result["plans"][0]["itinerary"]], expected)

    def test_explicit_backend_context_prevents_relative_duration_being_applied_twice(self):
        context = resolve_trip_changes("多玩一天", BASIC, "2026-10-10", "2026-10-12", TODAY)
        result = self.modify("多玩一天", trip_context=context)
        self.assertEqual(result["days"], 4)
        self.assertEqual(result["end_date"], "2026-10-13")
        self.assertEqual(len(result["plans"][0]["itinerary"]), 4)
        self.assertEqual(result["plans"][0]["itinerary"][-1]["schedule"], [])


class GlobalPlannerPriceTests(unittest.TestCase):
    def setUp(self):
        self.item = {"id": "s1", "type": "景点", "name": "原景点", "time": "09:00-11:00", "price": 110, "unit_price": 55, "price_basis": "per_person", "price_known": True, "price_source": "search"}
        self.original = {"destination": "杭州", "start_date": "2026-10-10", "end_date": "2026-10-10", "days": 1, "basic": BASIC, "plans": [{"style": "经典", "itinerary": [{"day": 1, "date": "2026-10-10", "schedule": [self.item]}]}]}

    def modify(self, schedule, instruction="总预算降到3000元", search=None):
        import plan
        reply = copy.deepcopy(self.original)
        reply["plans"][0]["itinerary"][0]["schedule"] = schedule
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reply)))])
        original_copy = copy.deepcopy(self.original)
        with patch("plan.OpenAI"), patch("plan.resolve_trip_changes", side_effect=lambda instruction, basic, start, end: resolve_trip_changes(instruction, basic, start, end, TODAY)), patch("plan._chat_completion", return_value=response):
            result = plan.modify_plan(self.original, {"mode": "global", "instruction": instruction}, search or {"destination": "杭州"}, basic=BASIC)
        self.assertEqual(self.original, original_copy)
        return result

    def test_budget_model_cannot_lower_same_place_verified_unit_price(self):
        changed = {**self.item, "price": 30, "unit_price": 15, "price_basis": "group", "price_known": False}
        result = self.modify([changed])
        item = result["plans"][0]["itinerary"][0]["schedule"][0]
        for field in ("price", "unit_price", "price_basis", "price_known", "price_source"):
            self.assertEqual(item[field], self.item[field])

    def test_new_name_cannot_inherit_old_quote_or_models_guess_and_free_flag(self):
        import plan
        changed = {**self.item, "name": "新景点", "price": 30, "unit_price": 30, "free": True}
        search = {"poi": [{"name": self.item["name"], "price": 55}]}
        result = self.modify([changed], search=search)
        item = result["plans"][0]["itinerary"][0]["schedule"][0]
        self.assertIsNone(item["price"])
        self.assertIsNone(item["unit_price"])
        self.assertFalse(item["price_known"])
        self.assertEqual(item["price_source"], "unknown")
        result["blocks"] = plan.blockify(result)
        priced = plan._attach_prices(result, search, 3000, BASIC)
        self.assertFalse(priced["blocks"][0]["price_known"])
        self.assertEqual(priced["budget_status"], "unknown")

    def test_changed_place_uses_real_source_55_instead_of_model_30(self):
        import plan
        changed = {**self.item, "name": "新景点", "price": 30, "unit_price": 30}
        search = {"poi": [{"name": "新景点", "price": 55}]}
        result = self.modify([changed], search=search)
        item = result["plans"][0]["itinerary"][0]["schedule"][0]
        self.assertEqual(item["unit_price"], 55)
        self.assertTrue(item["price_known"])
        self.assertEqual(item["price_source"], "search")
        result["blocks"] = plan.blockify(result)
        self.assertEqual(plan._attach_prices(result, search, 3000, BASIC)["total_cost"], 110)

    def test_only_source_free_is_treated_as_known_zero(self):
        changed = {**self.item, "name": "免费公园", "price": 30}
        result = self.modify([changed], search={"poi": [{"name": "免费公园", "free": True}]})
        item = result["plans"][0]["itinerary"][0]["schedule"][0]
        self.assertEqual(item["unit_price"], 0)
        self.assertTrue(item["price_known"])

    def test_named_explicit_user_quote_is_accepted_but_trip_budget_is_not_item_price(self):
        changed = {**self.item, "name": "景点乙", "price": 30}
        result = self.modify([changed], "景点乙门票55元，总预算降到3000元")
        item = result["plans"][0]["itinerary"][0]["schedule"][0]
        self.assertEqual(item["unit_price"], 55)
        self.assertEqual(item["price_source"], "manual")
        self.assertEqual(result["basic"]["total_budget"], 3000)

    def test_hand_selected_restaurant_keeps_price_coordinates_options_and_flags(self):
        meal = {"id": "m1", "type": "美食", "meal": "午餐", "name": "用户选的店", "time": "12:00-13:00", "price": 170, "unit_price": 85, "price_basis": "per_person", "price_known": True, "lng": 120.11, "lat": 30.21, "link": "https://example.test/chosen", "user_selected": True, "options": [{"name": "用户选的店", "price": 85, "lng": 120.11, "lat": 30.21, "link": "https://example.test/chosen"}]}
        self.original["plans"][0]["itinerary"][0]["schedule"] = [meal]
        guess = {**meal, "name": "模型建议的低价店", "price": 30, "unit_price": 30, "lng": 121, "lat": 31, "user_selected": False, "options": [{"name": "模型建议的低价店", "price": 30}]}
        result = self.modify([guess])
        selected = result["plans"][0]["itinerary"][0]["schedule"][0]
        for field in ("id", "name", "price", "unit_price", "price_known", "price_basis", "lng", "lat", "link", "options", "user_selected"):
            self.assertEqual(selected[field], meal[field])

    def test_budget_model_cannot_remove_locked_hotel_or_restore_different_hotel_metadata(self):
        hotel = {"id": "h1", "type": "酒店", "name": "锁定酒店", "time": "住宿", "price": 800, "unit_price": 800, "price_basis": "group", "price_known": True, "locked": True, "lng": 120.11, "lat": 30.21, "link": "https://example.test/hotel"}
        self.original["plans"][0]["itinerary"][0]["schedule"] = [hotel]
        result = self.modify([{"id": "model-hotel", "type": "酒店", "name": "模型猜的酒店", "time": "住宿", "price": 30}])
        day = result["plans"][0]["itinerary"][0]
        self.assertEqual(len(day["schedule"]), 1)
        self.assertEqual(day["schedule"][0]["name"], hotel["name"])
        self.assertEqual(day["schedule"][0]["unit_price"], 800)
        self.assertEqual(day["hotel"], hotel["name"])
        self.assertTrue(day["schedule"][0]["locked"])


if __name__ == "__main__":
    unittest.main()
