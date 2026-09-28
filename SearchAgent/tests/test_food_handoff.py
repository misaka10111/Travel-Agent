"""只验证餐饮条件与餐厅事实透传；不运行规划模型或 LangGraph。"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backend_plan():
    return _load_module("food_test_backend_plan", "backend/app/api/routes/plan.py")


@pytest.fixture
def orchestrator(monkeypatch):
    memory = types.ModuleType("langgraph.checkpoint.memory")
    memory.MemorySaver = type("FakeMemorySaver", (), {})
    graph = types.ModuleType("langgraph.graph")
    graph.END, graph.START, graph.StateGraph = "end", "start", type("FakeStateGraph", (), {})
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.memory", memory)
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph)
    return _load_module("food_test_orchestrator", "Orchestrator/orchestrator.py")


@pytest.fixture
def planner(monkeypatch):
    openai = types.ModuleType("openai")
    openai.OpenAI = type("FakeOpenAI", (), {})
    monkeypatch.setitem(sys.modules, "openai", openai)
    return _load_module("food_test_planner", "PlanAgent/plan.py")


def test_plan_endpoint_and_orchestrator_pass_food_options(backend_plan, orchestrator, monkeypatch):
    options = {
        "cuisines": ["川菜"], "max_price_per_person": 80, "min_rating": 4,
        "location": {"longitude": 120.15, "latitude": 30.25, "coordinate_system": "GCJ-02"},
    }
    captured = []

    def subprocess_run(_args, **kwargs):
        captured.append(json.loads(kwargs["input"]))
        return types.SimpleNamespace(stdout='{"plan":{}}', stderr="")

    monkeypatch.setattr(backend_plan.subprocess, "run", subprocess_run)
    request = backend_plan.PlanRequest.model_validate({
        "destination": "杭州", "start_date": "2026-10-05", "food_options": options,
        "basic": {"origin": "上海"}, "profile": {"travel_style": ["美食探店"]},
    })
    assert backend_plan.create_plan(request) == {"plan": {}}
    assert captured[0]["food_options"] == options
    search_payloads = []
    monkeypatch.setattr(orchestrator, "_call", lambda _python, _script, payload: search_payloads.append(payload) or {"food": []})
    assert orchestrator.search_node(captured[0]) == {"search": {"food": []}}
    assert search_payloads[0]["food_options"] == options
    assert search_payloads[0]["origin"] == "上海"
    assert "food_options" in orchestrator.State.__annotations__


def test_profile_does_not_generate_food_filters(backend_plan, orchestrator, monkeypatch):
    captured = []
    monkeypatch.setattr(backend_plan.subprocess, "run", lambda _args, **kwargs: (
        captured.append(json.loads(kwargs["input"])) or types.SimpleNamespace(stdout="{}", stderr="")
    ))
    request = backend_plan.PlanRequest(
        destination="杭州", start_date="2026-10-05", profile={"travel_style": ["美食探店"]},
        basic={"total_budget": 5000}, answers=[{"question": "预算", "answer": "便宜"}],
    )
    backend_plan.create_plan(request)
    assert "food_options" not in captured[0]
    searches = []
    monkeypatch.setattr(orchestrator, "_call", lambda _python, _script, payload: searches.append(payload) or {})
    orchestrator.search_node(captured[0])
    assert "food_options" not in searches[0]


def test_local_modify_reuses_explicit_food_options(backend_plan, monkeypatch):
    options = {"keywords": ["火锅"], "max_price_per_person": 120}
    search_result = {
        "food": [{"id": "sample", "name": "新餐厅", "url": "https://uri.amap.com/marker?poiid=sample",
                  "price_per_person": 90, "rating": 4.5}],
        "food_meta": {"warnings": ["价格仅供参考"], "source": "amap"},
    }
    captured = []

    def subprocess_run(_args, **kwargs):
        captured.append(json.loads(kwargs["input"]))
        output = search_result if len(captured) == 1 else {"blocks": []}
        return types.SimpleNamespace(stdout=json.dumps(output), stderr="")

    monkeypatch.setattr(backend_plan.subprocess, "run", subprocess_run)
    request = backend_plan.PlanRequest(
        destination="杭州", start_date="2026-10-05", end_date="2026-10-06", food_options=options,
        basic={"origin": "上海"}, modify={"blocks": [{"id": "lunch"}], "instruction": "换一家饭店"},
    )
    assert backend_plan.create_plan(request) == {"blocks": [], "search": search_result}
    assert captured[0]["food_options"] == options
    assert captured[0]["origin"] == "上海"
    assert captured[1]["search"] == search_result


def test_modify_blocks_uses_source_links_and_preserves_unchanged_names(planner, monkeypatch):
    old_url = "https://uri.amap.com/marker?poiid=old"
    new_url = "https://uri.amap.com/marker?poiid=new"
    blocks = [
        {"id": "same", "name": "原餐厅", "link": old_url},
        {"id": "change", "name": "原餐厅", "link": old_url},
        {"id": "unknown", "name": "原餐厅", "link": old_url},
        {"id": "same-empty", "name": "原餐厅", "link": ""},
    ]
    model_result = {"blocks": [
        {"id": "same", "name": "原餐厅", "link": "https://invented.example/same"},
        {"id": "change", "name": "新餐厅", "link": old_url},
        {"id": "unknown", "name": "没检索到的餐厅", "link": "https://invented.example/unknown"},
        {"id": "same-empty", "name": "原餐厅", "link": "https://invented.example/empty"},
    ]}
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=json.dumps(model_result, ensure_ascii=False)),
        )])

    monkeypatch.setattr(planner, "OpenAI", lambda **_kwargs: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)),
    ))
    search_result = {"food": [
        {"name": "原餐厅", "url": "https://uri.amap.com/marker?poiid=refreshed"},
        {"name": "新餐厅", "url": new_url, "price_per_person": 90, "rating": 4.5},
    ]}
    result = planner.modify_blocks(blocks, "换一家餐厅", search_result)

    assert [block["link"] for block in result["blocks"]] == [
        old_url, new_url, "", "https://uri.amap.com/marker?poiid=refreshed",
    ]
    assert [block["link"] for block in blocks] == [old_url, old_url, old_url, ""]
    sent_search = json.loads(calls[0]["messages"][1]["content"])["search"]
    assert sent_search["food"][1]["price_per_person"] == 90
    assert sent_search["food"][1]["rating"] == 4.5


@pytest.mark.parametrize("source, source_item", [
    ("hotels", {"name": "新酒店", "url": "https://example.com/hotel"}),
    ("poi", {"name": "新景点", "url": "https://example.com/poi"}),
])
def test_modify_blocks_backfills_other_search_sources(planner, monkeypatch, source, source_item):
    output = {"blocks": [{"id": "b1", "name": source_item["name"], "link": ""}]}
    monkeypatch.setattr(planner, "OpenAI", lambda **_kwargs: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            create=lambda **_kwargs: types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(output, ensure_ascii=False)),
            )]),
        )),
    ))
    result = planner.modify_blocks(
        [{"id": "b1", "name": "原活动", "link": "https://example.com/old"}],
        "换一个", {source: [source_item]},
    )
    assert result["blocks"][0]["link"] == source_item["url"]


@pytest.mark.parametrize("name, target, matched", [
    ("西湖餐厅", "西湖餐厅", True),
    ("西湖餐厅", "西湖餐厅（午餐）", True),
    ("西湖餐厅", "西湖餐厅 · 午餐", True),
    ("西湖餐厅", "西湖餐厅-午餐", True),
    ("西湖餐厅", "西湖餐厅：晚餐", True),
    ("西湖餐厅", "西湖餐厅—用餐", True),
    ("Cafe", "CAFE（下午茶）", True),
    ("西湖Ａ餐厅", "西 湖A 餐 厅", True),
    ("西湖餐厅", "西湖餐厅二店", False),
    ("西湖餐厅", "西湖餐厅（分店）", False),
    ("西湖餐厅二店", "西湖餐厅", False),
])
def test_food_links_match_full_names_or_explicit_meal_suffix(planner, name, target, matched):
    url = "https://uri.amap.com/marker?poiid=sample"
    block = {"name": target, "link": "https://invented.example"}
    plan = {"plans": [{"itinerary": [{"schedule": [block]}]}]}
    planner._backfill_links(plan, {"food": [{"name": name, "url": url}]})
    assert block["link"] == (url if matched else "")


@pytest.mark.parametrize("second_url", ["https://uri.amap.com/marker?poiid=other", None])
def test_food_links_do_not_guess_between_same_named_pois(planner, second_url):
    block = {"name": "西湖餐厅（午餐）"}
    plan = {"plans": [{"itinerary": [{"schedule": [block]}]}]}
    planner._backfill_links(plan, {"food": [
        {"id": "one", "name": "西湖餐厅", "url": "https://uri.amap.com/marker?poiid=one"},
        {"id": "two", "name": "西 湖餐厅", "url": second_url},
    ]})
    assert block["link"] == ""


def test_nonfood_links_keep_existing_hotel_prefix_matching(planner):
    block = {"name": "西湖酒店豪华房"}
    day = {"hotel": "西湖酒店豪华房", "schedule": [block]}
    plan = {"plans": [{"itinerary": [day]}]}
    url = "https://example.com/hotel"
    planner._backfill_links(plan, {"hotels": [{"name": "西湖酒店", "url": url}]})
    assert block["link"] == url
    assert day["hotel_link"] == url


def test_plan_trim_keeps_food_facts_and_metadata(planner):
    restaurant = {
        "id": "sample", "name": "餐厅", "title": "餐厅", "content": "说明" * 200,
        "address": "测试路1号", "location": {"longitude": 120.15, "latitude": 30.25, "coordinate_system": "GCJ-02"},
        "price_per_person": None, "currency": "CNY", "price_unit": "person/meal",
        "rating": 4.2, "rating_scale": 5, "cuisine": "浙江菜", "cuisine_source": "amap_type",
        "category": "餐饮服务;中餐厅;浙江菜", "typecode": "050106",
        "source": "amap", "fetched_at": "2026-10-05T08:00:00Z",
        "tags": ["特色菜"], "distance_m": 500.0, "distance_kind": "straight_line",
        "missing_fields": ["price_per_person", "reviews"], "website": "https://example.com",
    }
    metadata = {
        "warnings": ["价格缺失"], "resolved_query": {"destination": "杭州", "max_price_per_person": 80},
        "fetched_at": "2026-10-05T08:00:00Z", "source": "amap", "coordinate_system": "GCJ-02",
    }
    event = {"title": "展览", "content": "信息" * 200, "url": "https://example.com/event"}
    trimmed = planner._trim_search({"food": [restaurant], "food_meta": metadata, "events": [event]})
    assert trimmed["food"][0] == {key: value for key, value in restaurant.items() if key not in ("content", "website")} | {
        "content": restaurant["content"][:120],
    }
    assert trimmed["food_meta"] == metadata
    assert trimmed["food"][0]["price_per_person"] is None
    assert trimmed["events"] == [{"title": "展览", "content": event["content"][:120]}]
    assert restaurant["content"] == "说明" * 200
