"""Source identity and explicit unknown prices, without model/network calls."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan


def cost(blocks, sources, people=2):
    return plan._attach_prices({"blocks": copy.deepcopy(blocks)}, sources, 4000, {"travelers": f"{people}人"})


class PriceSourceTests(unittest.TestCase):
    def test_west_lake_does_not_inherit_west_lake_restaurant_price(self):
        result = cost([{"name": "西湖", "type": "景点"}], {
            "food": [{"name": "西湖小厨(杭帮菜.川湘菜)", "price_per_person": 55}],
            "hotels": [{"name": "西湖", "price": 800}],
        })
        self.assertIsNone(result["blocks"][0]["unit_price"])
        self.assertFalse(result["blocks"][0]["price_known"])
        self.assertEqual(result["total_cost"], 0)
        self.assertEqual(result["budget_status"], "unknown")

    def test_same_name_uses_only_its_own_resource_type(self):
        result = cost([{"name": "同名地点", "type": kind} for kind in ("景点", "美食", "酒店")], {
            "poi": [{"name": "同名地点", "price": 30}],
            "food": [{"name": "同名地点", "price_per_person": 55}],
            "hotels": [{"name": "同名地点", "price": 800}],
        })
        self.assertEqual([block["unit_price"] for block in result["blocks"]], [30, 55, 800])
        self.assertEqual([block["price"] for block in result["blocks"]], [60, 110, 800])
        self.assertEqual(result["total_cost"], 970)

    def test_same_type_prefix_is_not_a_price_match(self):
        result = cost([{"name": "西湖", "type": "景点"}], {
            "poi": [{"name": "西湖天地", "price": 50}, {"name": "西湖风景名胜区", "price": 80}],
        })
        self.assertIsNone(result["blocks"][0]["unit_price"])

    def test_different_museum_branches_are_not_price_aliases(self):
        for name in ("浙江省博物馆", "浙江省博物馆(孤山馆区)"):
            with self.subTest(name=name):
                result = cost([{"name": name, "type": "景点"}], {
                    "poi": [{"name": "浙江省博物馆(之江馆)", "price": 60}],
                })
                self.assertIsNone(result["blocks"][0]["unit_price"])

    def test_parenthesis_format_preserves_same_branch_identity(self):
        result = cost([{"name": "浙江省博物馆（孤山馆区）", "type": "景点"}], {
            "poi": [{"name": "浙江省博物馆(孤山馆区)", "free": True}],
        })
        self.assertEqual(result["blocks"][0]["unit_price"], 0)
        self.assertTrue(result["blocks"][0]["price_known"])

    def test_explicit_unknown_cannot_recover_stale_unit_or_source_price(self):
        sample = [{"name": "未核实餐厅", "type": "美食", "price_source": "unknown",
                   "unit_price": 600, "price": 1200, "price_known": True}]
        sources = {"food": [{"name": "未核实餐厅", "price_per_person": 70}]}
        result = cost(sample, sources)
        result = plan._attach_prices(result, sources, 4000, {"travelers": "2人"})
        self.assertIsNone(result["blocks"][0]["unit_price"])
        self.assertFalse(result["blocks"][0]["price_known"])
        self.assertEqual(result["blocks"][0]["price"], 0)
        self.assertEqual(result["unpriced_items"], {"推荐方案": ["未核实餐厅"]})

    def test_false_known_flag_discards_old_unit_and_old_group_basis(self):
        result = cost([{"name": "新餐厅", "type": "美食", "price_known": False,
                        "unit_price": 500, "price": 1000, "price_basis": "group"}], {
            "food": [{"name": "新餐厅", "price_per_person": 30}],
        })
        self.assertEqual(result["blocks"][0]["unit_price"], 30)
        self.assertEqual(result["blocks"][0]["price"], 60)
        self.assertEqual(result["blocks"][0]["price_basis"], "per_person")

    def test_false_known_flag_without_valid_source_stays_unknown(self):
        result = cost([{"name": "新餐厅", "type": "美食", "price_known": False,
                        "unit_price": 500, "price": 1000}], {})
        self.assertIsNone(result["blocks"][0]["unit_price"])
        self.assertFalse(result["blocks"][0]["price_known"])

    def test_selected_verified_price_is_preserved_and_idempotent(self):
        result = cost([{"name": "手选餐厅", "type": "美食", "user_selected": True,
                        "price_known": True, "unit_price": 80, "price": 160,
                        "price_basis": "per_person"}], {"food": [{"name": "手选餐厅", "price_per_person": 999}]})
        self.assertEqual(result["total_cost"], 160)
        result = plan._attach_prices(result, {}, 4000, {"travelers": "3人"})
        self.assertEqual(result["blocks"][0]["unit_price"], 80)
        self.assertEqual(result["total_cost"], 240)
        result = plan._attach_prices(result, {}, 4000, {"travelers": "3人"})
        self.assertEqual(result["total_cost"], 240)

    def test_unpriced_display_zero_is_not_a_free_admission(self):
        result = cost([{"name": "免费与否未知", "type": "景点", "unit_price": None,
                        "price": 0, "price_known": False}], {})
        self.assertFalse(result["blocks"][0]["price_known"])
        self.assertEqual(result["budget_status"], "unknown")

    def test_formatted_train_name_uses_verified_identifier_date_and_direction(self):
        source = {"transport": "高铁", "train_no": "G219", "direction": "去",
                  "dep_time": "2026-10-16 07:04:00", "price": 73}
        block = {"name": "去程：高铁G219 上海虹桥站→杭州东站", "type": "交通",
                 "direction": "去", "dep_time": "2026-10-16T07:04:00"}
        self.assertEqual(cost([block], {"trains": [source]})["blocks"][0]["price"], 146)
        for changed in ({"direction": "回"}, {"dep_time": "2026-10-17T07:04:00"},
                        {"name": "去程：高铁G2199 上海→杭州"}):
            with self.subTest(changed=changed):
                self.assertIsNone(cost([{**block, **changed}], {"trains": [source]})["blocks"][0]["unit_price"])

    def test_masked_quotes_and_boolean_prices_remain_unknown(self):
        for quote in ("¥8xx", "6x", True, float("inf"), float("nan")):
            with self.subTest(quote=quote):
                self.assertIsNone(cost([{"name": "酒店", "type": "酒店"}], {
                    "hotels": [{"name": "酒店", "price": quote}],
                })["blocks"][0]["unit_price"])
        self.assertEqual(cost([{"name": "酒店", "type": "酒店"}], {
            "hotels": [{"name": "酒店", "price": "¥350.00"}],
        })["blocks"][0]["price"], 350)

    def test_activity_uses_event_or_poi_without_restaurant_leak(self):
        result = cost([{"name": "夜间展览", "type": "活动"}], {
            "events": [{"title": "夜间展览", "price": 45}],
            "food": [{"name": "夜间展览", "price_per_person": 500}],
        })
        self.assertEqual(result["blocks"][0]["unit_price"], 45)

    def test_ambiguous_same_type_quotes_need_explicit_poi_identity(self):
        sources = {"food": [{"name": "同名饭店", "poi_id": "a", "price_per_person": 30},
                            {"name": "同名饭店", "poi_id": "b", "price_per_person": 90}]}
        self.assertIsNone(cost([{"name": "同名饭店", "type": "美食"}], sources)["blocks"][0]["unit_price"])
        self.assertEqual(cost([{"name": "同名饭店", "type": "美食", "poi_id": "b"}], sources)["blocks"][0]["unit_price"], 90)


if __name__ == "__main__":
    unittest.main()
