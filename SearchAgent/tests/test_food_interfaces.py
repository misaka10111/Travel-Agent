"""接口与综合检索契约测试；所有上游调用均替换，不消耗 API 配额。"""

import importlib.util
import io
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from SearchAgent import food_api, food_cli
from SearchAgent.food import FoodSearchError, FoodSearchRequest, FoodSearchResponse, Restaurant


@pytest.fixture
def response() -> FoodSearchResponse:
    timestamp = "2026-10-05T08:00:00+00:00"
    restaurant = Restaurant(
        id="sample-poi", name="测试餐厅", title="测试餐厅", content="浙江菜",
        address="测试路1号", location={"longitude": 120.15, "latitude": 30.25},
        price_per_person=None, rating=None, cuisine="浙江菜", cuisine_source="amap_type",
        category="餐饮服务;中餐厅;浙江菜", typecode="050106", tags=[],
        business_area=None, telephone=None, website=None,
        url="https://uri.amap.com/poidetail?poiid=sample-poi",
        distance_m=None, distance_kind=None,
        missing_fields=["price_per_person", "rating", "reviews"], fetched_at=timestamp,
    )
    return FoodSearchResponse(
        destination="杭州", resolved_query={"destination": "杭州"}, food=[restaurant],
        warnings=["部分餐厅缺少价格和评分"], fetched_at=timestamp,
    )


def test_api_success_keeps_facts_and_metadata(monkeypatch, response):
    calls = []

    def search(request):
        calls.append(request)
        return response

    monkeypatch.setattr(food_api, "search_restaurants", search)
    result = TestClient(food_api.app).post(
        "/api/search/food", json={"destination": "杭州", "max_price_per_person": 100, "limit": 5},
    )
    assert result.status_code == 200
    assert result.json() == response.model_dump(mode="json")
    assert result.json()["food"][0]["price_per_person"] is None
    assert result.json()["food"][0]["location"]["coordinate_system"] == "GCJ-02"
    assert calls[0].max_price_per_person == 100
    assert calls[0].limit == 5


def test_health_and_cors_do_not_call_upstream(monkeypatch):
    def unexpected_call(_request):
        pytest.fail("health/preflight must not call the food provider")

    monkeypatch.setattr(food_api, "search_restaurants", unexpected_call)
    client = TestClient(food_api.app)
    assert client.get("/health").json() == {"status": "ok", "service": "food-search"}
    allowed = client.options("/api/search/food", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    rejected = client.options("/api/search/food", headers={
        "Origin": "https://unconfigured.example", "Access-Control-Request-Method": "POST",
    })
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
    schema = client.get("/openapi.json").json()
    errors = schema["paths"]["/api/search/food"]["post"]["responses"]
    for status in (422, 500, 502, 503, 504):
        assert errors[str(status)]["content"]["application/json"]["schema"]["$ref"].endswith("FoodAPIErrorResponse")


@pytest.mark.parametrize("status,code", [
    (503, "AMAP_NOT_CONFIGURED"), (502, "AMAP_API_ERROR"),
    (504, "AMAP_TIMEOUT"), (422, "QUERY_PARSE_FAILED"),
])
def test_api_maps_public_food_errors(monkeypatch, status, code):
    def search(_request):
        raise FoodSearchError(code, "可公开的错误消息", status)

    monkeypatch.setattr(food_api, "search_restaurants", search)
    result = TestClient(food_api.app).post("/api/search/food", json={"destination": "杭州"})
    assert result.status_code == status
    assert result.json() == {"error": {"code": code, "message": "可公开的错误消息"}}


@pytest.mark.parametrize("payload", [
    {}, {"destination": "杭州", "limit": 0}, {"destination": "杭州", "radius_m": True},
    {"destination": "杭州", "location": {"longitude": 120, "latitude": 30, "coordinate_system": "WGS-84"}},
    {"destination": "杭州", "api_key": "private-input-value"},
])
def test_api_validation_rejects_bad_fields_without_echoing_input(payload):
    result = TestClient(food_api.app).post("/api/search/food", json=payload)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "invalid_request"
    assert result.json()["error"]["details"]
    assert "private-input-value" not in result.text
    assert all("input" not in detail for detail in result.json()["error"]["details"])


def test_api_unexpected_error_is_sanitized(monkeypatch):
    def search(_request):
        raise RuntimeError("upstream-url?key=private-upstream-value")

    monkeypatch.setattr(food_api, "search_restaurants", search)
    result = TestClient(food_api.app, raise_server_exceptions=False).post(
        "/api/search/food", json={"destination": "杭州"},
    )
    assert result.status_code == 500
    assert result.json()["error"]["code"] == "internal_error"
    assert "private-upstream-value" not in result.text


def test_cors_env_requires_json_array(monkeypatch):
    monkeypatch.setenv("FOOD_CORS_ORIGINS", '["https://app.example"]')
    assert food_api._cors_origins() == ["https://app.example"]
    monkeypatch.setenv("FOOD_CORS_ORIGINS", "https://app.example")
    with pytest.raises(ValueError, match="JSON"):
        food_api._cors_origins()


@pytest.mark.parametrize("mode", ["json", "positional_json", "stdin", "file", "query", "positional_query"])
def test_cli_input_modes(monkeypatch, capsys, tmp_path, response, mode):
    requests = []

    def search(request):
        requests.append(request)
        return response

    monkeypatch.setattr(food_cli, "search_restaurants", search)
    raw_json = json.dumps({"destination": "杭州", "limit": 3}, ensure_ascii=False)
    query = "杭州找三家餐厅"
    if mode == "json":
        argv = ["--json", raw_json]
    elif mode == "positional_json":
        argv = [raw_json]
    elif mode == "stdin":
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw_json))
        argv = []
    elif mode == "file":
        request_file = tmp_path / "request.json"
        request_file.write_text(raw_json, encoding="utf-8")
        argv = ["--file", str(request_file)]
    elif mode == "query":
        argv = ["--query", query]
    else:
        argv = [query]
    assert food_cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == response.model_dump(mode="json")
    assert len(requests) == 1
    if "query" in mode:
        assert requests[0].query == query
    else:
        assert requests[0].destination == "杭州"
        assert requests[0].limit == 3


@pytest.mark.parametrize("argv", [
    ["--json", "{"], ["--json", "[]"], ["--json", '{"destination":"杭州","api_key":"private-input-value"}'],
])
def test_cli_invalid_input_is_json_error(capsys, argv):
    assert food_cli.main(argv) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "invalid_request"
    assert "private-input-value" not in output


def test_cli_public_and_unexpected_errors(monkeypatch, capsys):
    def search(_request):
        raise FoodSearchError("AMAP_NOT_CONFIGURED", "请配置 AMAP_API_KEY", 503)

    monkeypatch.setattr(food_cli, "search_restaurants", search)
    assert food_cli.main(["--json", '{"destination":"杭州"}']) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "AMAP_NOT_CONFIGURED"

    def unexpected(_request):
        raise RuntimeError("private-upstream-value")

    monkeypatch.setattr(food_cli, "search_restaurants", unexpected)
    assert food_cli.main(["--query", "杭州美食"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "internal_error"
    assert "private-upstream-value" not in output


def test_cli_module_and_direct_script_schema_match():
    project_root = Path(__file__).resolve().parents[2]
    module = subprocess.run(
        [sys.executable, "-m", "SearchAgent.food_cli", "--schema"],
        cwd=project_root, capture_output=True, text=True, check=True,
    )
    direct = subprocess.run(
        [sys.executable, "SearchAgent/food_cli.py", "--schema"],
        cwd=project_root, capture_output=True, text=True, check=True,
    )
    assert json.loads(module.stdout) == json.loads(direct.stdout)
    assert json.loads(module.stdout)["request"]["additionalProperties"] is False


@pytest.fixture
def tools_module(monkeypatch):
    """其他工具客户端不属于独立餐饮依赖；以替身隔离这些外部组件。"""
    class FakeMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self, **_kwargs):
            return lambda function: function

    modules = {
        "mcp.server.fastmcp": ("FastMCP", FakeMCP),
        "openai": ("OpenAI", type("FakeOpenAI", (), {})),
        "tavily": ("TavilyClient", type("FakeTavily", (), {})),
    }
    for name, (attribute, value) in modules.items():
        module = types.ModuleType(name)
        setattr(module, attribute, value)
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[1] / "tools.py"
    spec = importlib.util.spec_from_file_location("SearchAgent._food_test_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("_fetch_weather", "_fetch_hotels", "_fetch_poi", "_fetch_promotions", "_fetch_events"):
        monkeypatch.setattr(module, name, lambda *_args: [])
    return module


def test_mcp_and_fetch_food_preserve_structured_response(monkeypatch, tools_module, response):
    monkeypatch.setattr(tools_module, "search_restaurants", lambda _request: response)
    assert tools_module._fetch_food("杭州") == response.model_dump(mode="json")["food"]
    assert json.loads(tools_module.search_food({"destination": "杭州"})) == response.model_dump(mode="json")


def test_food_options_reject_unknown_fields_and_use_trip_destination(tools_module):
    request = tools_module._food_request("杭州", food_options={"destination": "上海", "query": "找五家川菜"})
    assert request.destination == "杭州"
    assert "limit" not in request.model_fields_set
    with pytest.raises(FoodSearchError, match="参数不合法"):
        tools_module._food_request("杭州", food_options={"unknown": True})
    with pytest.raises(ValueError, match="food_options"):
        tools_module._food_request("杭州", food_options=[])


def test_run_search_preserves_food_meta_and_separates_cache_conditions(monkeypatch, tools_module, response):
    cache_reads, cache_writes, requests = [], [], []
    monkeypatch.setattr(tools_module, "_read_cache", lambda key: cache_reads.append(key))
    monkeypatch.setattr(tools_module, "_write_cache", lambda key, result: cache_writes.append((key, result)))

    def search(request):
        requests.append(request)
        return response

    monkeypatch.setattr(tools_module, "search_restaurants", search)
    base = {"destination": "杭州", "start_date": "2026-10-05"}
    for options in (
        {"query": "找五家餐厅"}, {"query": "找五家餐厅", "min_rating": None},
        {"max_price_per_person": 50}, {"max_price_per_person": 100},
    ):
        result = tools_module.run_search({**base, "food_options": options})
        assert result["food"] == response.model_dump(mode="json")["food"]
        assert result["food_meta"] == {
            field: response.model_dump(mode="json")[field]
            for field in ("warnings", "resolved_query", "fetched_at", "source", "coordinate_system")
        }
    assert len(set(cache_reads)) == 4
    assert len(cache_writes) == 4
    assert "limit" not in requests[0].model_fields_set
    assert "min_rating" not in requests[0].model_fields_set
    assert "min_rating" in requests[1].model_fields_set
    key = tools_module._cache_key("杭州", "a", "b", "", {"limit": 5, "radius_m": 1000})
    assert key == tools_module._cache_key("杭州", "a", "b", "", {"radius_m": 1000, "limit": 5})


def test_run_search_does_not_cache_food_failures(monkeypatch, tools_module):
    cache_writes = []
    monkeypatch.setattr(tools_module, "_read_cache", lambda _key: None)
    monkeypatch.setattr(tools_module, "_write_cache", lambda *_args: cache_writes.append(True))

    def search(_request):
        raise FoodSearchError("AMAP_TIMEOUT", "高德请求超时", 504)

    monkeypatch.setattr(tools_module, "search_restaurants", search)
    result = tools_module.run_search({"destination": "杭州", "start_date": "2026-10-05"})
    assert result["food"] == {"error": "高德请求超时"}
    assert cache_writes == []


def test_mcp_and_trip_validation_do_not_echo_sensitive_values(tools_module):
    response = tools_module.search_food({"destination": "杭州", "api_key": "private-input-value"})
    assert json.loads(response)["error"]["code"] == "invalid_request"
    assert "private-input-value" not in response
    with pytest.raises(FoodSearchError) as error:
        tools_module.run_search({
            "destination": "杭州", "start_date": "2026-10-05",
            "food_options": {"api_key": "private-input-value"},
        })
    assert error.value.http_status == 422
    assert "private-input-value" not in str(error.value)
