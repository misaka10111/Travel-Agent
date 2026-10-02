"""餐饮搜索智能体测试。

验证：
1. 高德地图 POI 搜索能返回结构化餐厅数据
2. 餐厅数据包含规划智能体所需的全部字段（含地图链接和 POI 详情链接）
3. 地图链接和 POI 详情链接格式正确
4. tools._fetch_food 能正确调用高德并在失败时降级到 Tavily
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# 确保能 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from amap_service import (  # noqa: E402
    RestaurantInfo,
    _build_map_url,
    _build_poi_detail_url,
    _parse_cuisine,
    search_restaurants,
)


def _has_amap_key() -> bool:
    return bool(os.getenv("AMAP_KEY"))


@unittest.skipUnless(_has_amap_key(), "未配置 AMAP_KEY，跳过网络测试")
class TestAmapRestaurantSearch(unittest.TestCase):
    """高德地图餐厅搜索集成测试。"""

    def test_search_returns_structured_data(self):
        restaurants = search_restaurants("杭州", limit=10)
        self.assertGreater(len(restaurants), 0, "杭州应该能搜到餐厅")

        for r in restaurants:
            self.assertTrue(r.name, f"餐厅名不能为空: {r}")
            self.assertIsInstance(r.longitude, float)
            self.assertIsInstance(r.latitude, float)
            self.assertTrue(r.address, f"地址不能为空: {r.name}")
            # 经纬度合理范围（中国境内）
            self.assertTrue(70 < r.longitude < 140, f"经度异常: {r.longitude}")
            self.assertTrue(15 < r.latitude < 55, f"纬度异常: {r.latitude}")

    def test_restaurant_has_required_fields(self):
        restaurants = search_restaurants("杭州", limit=5)
        r = restaurants[0]
        d = r.to_dict()
        required = {
            "name", "address", "longitude", "latitude", "cuisine",
            "rating", "price_per_person", "business_area",
            "poi_id", "map_url", "poi_detail_url",
        }
        self.assertEqual(set(d.keys()), required)

    def test_map_url_format(self):
        url = _build_map_url(120.1551, 30.2741, "楼外楼")
        self.assertIn("uri.amap.com/marker", url)
        self.assertIn("position=120.1551,30.2741", url)
        self.assertIn("name=", url)

    def test_poi_detail_url_format(self):
        self.assertEqual(
            _build_poi_detail_url("B0FFFABCD"),
            "https://www.amap.com/place/B0FFFABCD",
        )
        self.assertEqual(_build_poi_detail_url(""), "")

    def test_restaurant_has_links(self):
        """每家餐厅都必须有地图链接和 POI 详情链接。"""
        restaurants = search_restaurants("杭州", limit=5)
        for r in restaurants:
            self.assertTrue(r.map_url, f"{r.name} 缺少地图链接")
            self.assertTrue(r.poi_detail_url, f"{r.name} 缺少 POI 详情链接")
            self.assertTrue(r.map_url.startswith("https://uri.amap.com/marker"))
            self.assertTrue(r.poi_detail_url.startswith("https://www.amap.com/place/"))

    def test_cuisine_parsing(self):
        self.assertEqual(_parse_cuisine("餐饮服务;中餐厅;川菜"), "川菜")
        self.assertEqual(_parse_cuisine("餐饮服务;外国餐厅;日本料理"), "日本料理")
        self.assertEqual(_parse_cuisine("餐饮服务;快餐厅"), "快餐厅")
        self.assertEqual(_parse_cuisine(""), "")

    def test_keyword_search(self):
        restaurants = search_restaurants("杭州", keyword="火锅", limit=5)
        self.assertGreater(len(restaurants), 0)
        cuisines = " ".join(r.cuisine for r in restaurants)
        names = " ".join(r.name for r in restaurants)
        self.assertTrue(
            "火锅" in cuisines or "火锅" in names,
            f"关键词搜索结果应含火锅: {cuisines} / {names}",
        )

    def test_to_dict_serializable(self):
        restaurants = search_restaurants("杭州", limit=3)
        for r in restaurants:
            d = r.to_dict()
            json.dumps(d, ensure_ascii=False)


class TestRestaurantInfoLocal(unittest.TestCase):
    """本地单元测试，不依赖网络。"""

    def test_default_values(self):
        r = RestaurantInfo(name="测试", address="测试地址", longitude=120.0, latitude=30.0)
        self.assertEqual(r.cuisine, "")
        self.assertEqual(r.rating, 0.0)
        self.assertEqual(r.price_per_person, 0.0)
        self.assertEqual(r.poi_id, "")
        self.assertEqual(r.map_url, "")
        self.assertEqual(r.poi_detail_url, "")

    def test_to_dict_contains_all_fields(self):
        r = RestaurantInfo(
            name="楼外楼",
            address="杭州市西湖区孤山路30号",
            longitude=120.1551,
            latitude=30.2741,
            cuisine="江浙菜",
            rating=4.5,
            price_per_person=188.0,
            business_area="西湖",
            poi_id="B0FFFABCD",
            map_url="https://uri.amap.com/marker?position=120.1551,30.2741&name=楼外楼",
            poi_detail_url="https://www.amap.com/place/B0FFFABCD",
        )
        d = r.to_dict()
        self.assertEqual(d["name"], "楼外楼")
        self.assertEqual(d["cuisine"], "江浙菜")
        self.assertEqual(d["rating"], 4.5)
        self.assertEqual(d["price_per_person"], 188.0)


class TestFetchFoodFallback(unittest.TestCase):
    """验证 tools._fetch_food 在高德失败时回退到 Tavily。"""

    def test_fallback_on_error(self):
        try:
            import tools  # noqa: F401
        except ImportError:
            self.skipTest("tools.py 依赖未安装（需要 Python >= 3.10 和 mcp/langchain）")
        with patch("amap_service.search_restaurants", side_effect=RuntimeError("amap error")):
            with patch("tools._fetch_web_search", return_value=[]):
                with patch("tools._extract_item_features", side_effect=lambda items, prompt: items):
                    import tools
                    result = tools._fetch_food("杭州", max_results=5)
                    self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
