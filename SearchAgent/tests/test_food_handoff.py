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
    captured = []

    def subprocess_run(_args, **kwargs):
        captured.append(json.loads(kwargs["input"]))
        output = {"food": [{"id": "sample"}]} if len(captured) == 1 else {"blocks": []}
        return types.SimpleNamespace(stdout=json.dumps(output), stderr="")

    monkeypatch.setattr(backend_plan.subprocess, "run", subprocess_run)
    request = backend_plan.PlanRequest(
        destination="杭州", start_date="2026-10-05", end_date="2026-10-06", food_options=options,
        basic={"origin": "上海"}, modify={"blocks": [{"id": "lunch"}], "instruction": "换一家饭店"},
    )
    assert backend_plan.create_plan(request) == {"blocks": []}
    assert captured[0]["food_options"] == options
    assert captured[0]["origin"] == "上海"
    assert captured[1]["search"] == {"food": [{"id": "sample"}]}


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
