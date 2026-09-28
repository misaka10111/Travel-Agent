"""Provider contract and filtering tests; no real keys or network required."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from SearchAgent.food import FoodSearchError, FoodSearchRequest, search_restaurants


@pytest.fixture(autouse=True)
def provider_config(monkeypatch):
    monkeypatch.setenv("AMAP_API_KEY", "amap-test-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test-secret")
    monkeypatch.setenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("QWEN_MODEL", "qwen-max")


def poi(poi_id="restaurant-1", **overrides):
    result = {
        "id": poi_id, "name": "测试餐厅", "type": "餐饮服务;中餐厅;浙江菜",
        "typecode": "050106", "address": "测试路1号", "location": "120.150000,30.250000",
        "biz_ext": {"cost": "80", "rating": "4.5"}, "tag": "龙井虾仁,东坡肉",
    }
    result.update(overrides)
    return result


def run(payload, handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return search_restaurants(FoodSearchRequest.model_validate(payload), client=client)


def amap_response(request, items):
    return httpx.Response(200, json={"status": "1", "count": str(len(items)), "pois": items})


def test_real_poi_facts_and_safe_map_link():
    def handler(request):
        assert request.url.path == "/v3/place/text"
        assert request.url.params["types"] == "050000"
        assert request.url.params["citylimit"] == "true"
        assert request.url.params["extensions"] == "all"
        assert request.url.params["offset"] == "25"
        return amap_response(request, [poi()])
    result = run({"destination": "杭州"}, handler)
    item = result.food[0]
    assert item.price_per_person == 80 and item.rating == 4.5
    assert item.cuisine == "浙江菜" and item.cuisine_source == "amap_type"
    assert item.location.longitude == 120.15 and item.location.latitude == 30.25
    assert item.location.coordinate_system == "GCJ-02"
    assert item.tags == ["龙井虾仁", "东坡肉"]
    assert item.reviews is None and item.opening_hours is None
    assert item.title == item.name
    assert parse_qs(urlsplit(item.url).query)["poiid"] == [item.id]
    assert "key" not in item.url and "amap-test-secret" not in result.model_dump_json()


def test_missing_array_and_zero_values_are_unknown_not_free():
    result = run({"destination": "杭州"}, lambda request: amap_response(request, [
        poi("unknown", biz_ext={"cost": [], "rating": []}, location=[], type="餐饮服务;中餐厅;综合酒楼"),
        poi("zero", biz_ext={"cost": "0", "rating": "0"}),
    ]))
    assert len(result.food) == 2
    assert all(x.price_per_person is None and x.rating is None for x in result.food)
    item = next(x for x in result.food if x.id == "unknown")
    assert item.location is None and item.cuisine is None
    assert {"price_per_person", "rating", "location", "cuisine"} <= set(item.missing_fields)


def test_price_rating_filters_do_not_silently_relax():
    items = [poi("valid"), poi("expensive", biz_ext={"cost": "101", "rating": "5"}),
             poi("low-rating", biz_ext={"cost": "60", "rating": "3"}),
             poi("unknown", biz_ext={"cost": [], "rating": []})]
    result = run({"destination": "杭州", "max_price_per_person": 100, "min_rating": 4},
                 lambda request: amap_response(request, items))
    assert [x.id for x in result.food] == ["valid"]
    empty = run({"destination": "杭州", "max_price_per_person": 1},
                lambda request: amap_response(request, items))
    assert empty.food == [] and any("未自动放宽" in x for x in empty.warnings)


def test_unknown_values_only_pass_filters_when_explicitly_enabled():
    result = run({"destination": "杭州", "max_price_per_person": 100, "min_rating": 4,
                  "include_unknown_price": True, "include_unknown_rating": True},
                 lambda request: amap_response(request, [poi("unknown", biz_ext={})]))
    assert len(result.food) == 1
    assert any("不能确认符合预算" in x for x in result.warnings)


def test_around_uses_gcj_coordinates_and_filters_radius_and_unknown_location():
    def handler(request):
        assert request.url.path == "/v3/place/around"
        assert request.url.params["location"] == "120.150000,30.250000"
        assert request.url.params["radius"] == "3000"
        return amap_response(request, [poi("near"), poi("far", location="121,31"), poi("unknown", location=[])])
    result = run({"destination": "杭州", "location": {"longitude": 120.15, "latitude": 30.25}}, handler)
    assert [x.id for x in result.food] == ["near"]
    assert result.food[0].distance_m == 0
    assert result.food[0].distance_kind == "straight_line"


def test_qwen_max_extracts_query_and_explicit_fields_override_even_null():
    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            assert request.headers["authorization"] == "Bearer qwen-test-secret"
            assert json.loads(request.content)["model"] == "qwen-max"
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "destination": "上海", "max_price_per_person": 20, "min_rating": 5, "limit": 2,
            })}}]})
        assert request.url.params["city"] == "杭州"
        return amap_response(request, [poi()])
    result = run({"query": "杭州人均100以内", "destination": "杭州", "max_price_per_person": 100,
                  "min_rating": None}, handler)
    assert len(result.food) == 1
    assert result.resolved_query["max_price_per_person"] == 100
    assert result.resolved_query["min_rating"] is None
    assert result.resolved_query["limit"] == 2
    assert result.resolved_query["query"] == "杭州人均100以内"


def test_unverified_dietary_query_is_preserved_for_plan():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"destination":"杭州"}'}}]})
        return amap_response(request, [poi()])
    result = run({"query": "杭州吃饭，花生过敏"}, handler)
    assert result.resolved_query["query"] == "杭州吃饭，花生过敏"
    assert any("未由高德数据验证" in x for x in result.warnings)


@pytest.mark.parametrize("content", ["not-json", '[]', '{"location":{"longitude":120,"latitude":30}}', '{"destination":"杭州","min_rating":9}', '{}'])
def test_bad_model_output_is_not_used_for_unconstrained_search(content):
    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    with pytest.raises(FoodSearchError) as exc:
        run({"query": "请帮我找餐厅"}, handler)
    assert exc.value.code == "QUERY_PARSE_FAILED" and exc.value.http_status == 422


def test_upstream_failures_do_not_leak_keys_or_response_bodies():
    with pytest.raises(FoodSearchError) as exc:
        run({"destination": "杭州"}, lambda request: httpx.Response(200, json={
            "status": "0", "infocode": "10001", "info": "amap-test-secret"}))
    assert "10001" in str(exc.value)
    assert "amap-test-secret" not in str(exc.value)
    with pytest.raises(FoodSearchError) as exc:
        run({"query": "杭州"}, lambda request: httpx.Response(401, text="qwen-test-secret"))
    assert exc.value.code == "QWEN_HTTP_ERROR" and "qwen-test-secret" not in str(exc.value)


def test_timeout_has_504_and_no_raw_request_url():
    def handler(request):
        raise httpx.ReadTimeout(str(request.url), request=request)
    with pytest.raises(FoodSearchError) as exc:
        run({"destination": "杭州"}, handler)
    assert exc.value.http_status == 504 and "amap-test-secret" not in str(exc.value)


def test_paging_deduplication_limit_and_truncation_are_explicit():
    calls = []
    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        # Same ID on every page is deduplicated; remaining records differ.
        items = [poi("duplicate"), *[poi(f"{page}-{i}") for i in range(24)]]
        return httpx.Response(200, json={"status": "1", "count": "1000", "pois": items})
    result = run({"destination": "杭州", "limit": 5}, handler)
    assert calls == [1, 2, 3]
    assert len(result.food) == 5 and len({x.id for x in result.food}) == 5
    assert result.resolved_query["candidate_count"] == 73
    assert result.resolved_query["truncated"] is True


def test_partial_query_failure_keeps_verified_results_with_warning():
    def handler(request):
        if request.url.params["keywords"] == "失败词":
            return httpx.Response(503)
        return amap_response(request, [poi()])
    result = run({"destination": "杭州", "keywords": ["成功词", "失败词"]}, handler)
    assert len(result.food) == 1
    assert result.resolved_query["truncated"] is True
    assert any("部分高德检索失败" in x for x in result.warnings)


def test_bad_poi_fields_do_not_break_the_batch_or_invent_cuisine():
    result = run({"destination": "杭州"}, lambda request: amap_response(request, [
        poi("street", name="测试美食街"), poi("bad-id", id=[]),
        poi("bad-website", website="http://[invalid", location="NaN,30", biz_ext={"cost": "NaN", "rating": "6"}),
        poi("tag", type="餐饮服务;中餐厅;特色/地方风味餐厅", tag="杭帮菜,东坡肉"),
        poi("unknown", name="名字包含川菜", type="餐饮服务;中餐厅;综合酒楼"),
    ]))
    assert len(result.food) == 3
    item = next(x for x in result.food if x.id == "bad-website")
    assert item.website is None and item.location is None and item.rating is None and item.price_per_person is None
    tag = next(x for x in result.food if x.id == "tag")
    assert tag.cuisine == "杭帮菜" and tag.cuisine_source == "amap_tag"
    assert next(x for x in result.food if x.id == "unknown").cuisine is None


@pytest.mark.parametrize("payload", [
    {}, {"destination": "  "}, {"destination": "杭州", "api_key": "do-not-echo"},
    {"destination": "杭州", "limit": 51}, {"destination": "杭州", "radius_m": 0},
    {"destination": "杭州", "max_price_per_person": -1}, {"destination": "杭州", "min_rating": 6},
    {"destination": "杭州", "location": {"longitude": 120, "latitude": 30, "coordinate_system": "WGS84"}},
    {"destination": "杭州", "keywords": ["foo|bar"]},
])
def test_invalid_frontend_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        FoodSearchRequest.model_validate(payload)


def test_amap_missing_config_is_actionable_and_no_network(monkeypatch):
    monkeypatch.delenv("AMAP_API_KEY")
    with pytest.raises(FoodSearchError) as exc:
        run({"destination": "杭州"}, lambda request: pytest.fail("Must not call upstream"))
    assert exc.value.code == "AMAP_NOT_CONFIGURED" and exc.value.http_status == 503
