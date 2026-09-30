"""Spatial invariants, delivery gates and the real HTTP/SQL action loop."""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
import httpx

from app.planning.clustering import identity_groups
from app.planning.edits import edit
from app.planning.engine import compute
from app.planning.validation import validate
from app.providers.maps.amap import AmapProvider
from app.providers.maps.base import RouteQuery, route_result
from app.providers.maps.transport import MapTransport
from app.providers.travel import TravelServices, normalize_offers
from app.runtime.guards import PlanningError, check_action
from app.runtime.store import SessionStore
from app.schemas.action import AGENT_ACTION_ADAPTER
from app.schemas.intent import TripIntent
from app.schemas.itinerary import ItineraryDraft
from app.schemas.place import Place
from app.schemas.session import SessionState
from app.config import Settings
from tests.test_p1_runtime import harness, run, wait_state


REGION = {"label": "北京", "country_code": "CN", "timezone": "Asia/Shanghai"}


def test_model_projection_preserves_route_evidence_without_polyline_payload():
    from app.agents.client import model_context
    route = {"geometry": {"points": [1, 2, 3]}, "duration_seconds": 123,
        "status": "ok", "from_place_id": "a", "to_place_id": "b"}
    reduced = model_context({"routes": [route]})
    assert "geometry" not in reduced["routes"][0]
    assert reduced["routes"][0]["duration_seconds"] == 123
    assert "geometry" in route


def test_unchanged_routes_reuse_only_live_same_session_observations():
    from app.planning.routing import RoutePlanner
    from app.schemas.common import utc_now
    from datetime import timedelta
    session, provider = state(), Routes()
    first, second = session.candidates[:2]
    departure = utc_now()
    route = RoutePlanner(provider, session.intent_snapshot, 2).leg(first, second, departure)
    reused = RoutePlanner(provider, session.intent_snapshot, 2, [route])
    assert reused.leg(first, second, departure).status == "ok"
    assert len(provider.calls) == 1
    reused.leg(first, second, departure + timedelta(minutes=1))
    assert len(provider.calls) == 2


def test_transfer_times_constrain_first_and_last_day_and_reject_stale_quotes():
    from app.schemas.common import utc_now
    from datetime import timedelta
    session = state()
    session.intent_snapshot.dates.start_date = datetime(2026, 10, 1).date()
    station = place("station", "北京南站", "transport")
    session.candidates.append(station)
    session.travel_offers = [{"offer_id": direction, "category": "train", "direction": direction,
        "observed_at": utc_now().isoformat(),
        "destination_place_id": "station", "origin_place_id": "station",
        "segments": [{"departure_at": dep, "arrival_at": arr}]} for direction, dep, arr in [
        ("outbound", "2026-10-01T06:00:00+08:00", "2026-10-01T11:00:00+08:00"),
        ("inbound", "2026-10-05T17:00:00+08:00", "2026-10-05T22:00:00+08:00")]]
    plan = compute(session, Routes(), 60)
    assert plan.days[0].stops[0].place_id == "station"
    assert plan.days[0].stops[0].start_minute == 660
    assert plan.days[-1].stops[-1].place_id == "station"
    assert plan.days[-1].departure_deadline_minute == 1020
    issues = validate(plan, session).issues
    assert any(i.code == "departure_connection_missed" for i in issues)
    assert not any(i.code == "hotel_roundtrip_missing" for i in issues)
    for offer in session.travel_offers:
        offer["observed_at"] = (utc_now() - timedelta(hours=2)).isoformat()
    assert compute(session, Routes(), 60).selected_offer_ids == []


def test_meals_follow_separate_attraction_anchors():
    session = state()
    session.intent_snapshot.dates.duration_days = 1
    session.candidates = [place("a", lon=116.3), place("b", lon=116.5),
        place("r1", category="restaurant", lon=116.301),
        place("r2", category="restaurant", lon=116.501)]
    plan = compute(session, Routes(), 60)
    stops = plan.days[0].stops
    visits = [s.place_id for s in stops]
    assert visits in (["a", "r1", "b", "r2"], ["b", "r2", "a", "r1"])


def test_far_restaurants_are_missing_coverage_not_forced_detours():
    session = state()
    session.candidates = [p for p in session.candidates if p.category != "restaurant"] + [
        place("far-food", category="restaurant", lon=117.0)]
    provider = Routes()
    plan = compute(session, provider, 60)
    assert not any(s.category == "restaurant" for d in plan.days for s in d.stops)
    assert provider.calls == []
    issues = validate(plan, session).issues
    assert sum(i.code == "meals_missing" for i in issues) == 5
    assert all(i.place_ids for i in issues if i.code == "meals_missing")


def test_unknown_destination_can_be_clarified_but_deferred_dates_are_not_reasked():
    session = state()
    session.intake_state.unknown_fields = ["destination", "dates", "budget"]
    def question(field):
        return AGENT_ACTION_ADAPTER.validate_python({"kind": "ask_user", "action_id": "q",
            "base_state_version": 1, "purpose": "确认需求", "gap_ids": [field], "question_goal": "确认需求"})
    check_action(question("destination"), session)
    with pytest.raises(PlanningError, match="question_already_unknown_or_declined"):
        check_action(question("dates"), session)


def place(pid, name=None, category="attraction", lon=116.4, lat=39.9):
    return Place(place_id=pid, name=name or pid, category=category, region=REGION,
        coordinates={"longitude": lon, "latitude": lat, "crs": "GCJ02"}, identity_status="candidate")


def candidates():
    return [place("a" + str(i), "古建景点" + str(i), lon=116.4 + i * .001) for i in range(10)] + [
        place("h", "住宿", "hotel"), place("r1", "餐馆1", "restaurant"), place("r2", "餐馆2", "restaurant", lon=116.403)]


def state():
    points = candidates()
    return SessionState(session_id="s", owner_ref="test", state_version=1, task_scope="itinerary",
        intent_snapshot=TripIntent(intent_id="i", destination=REGION, origin={"label": "上海", "country_code": "CN"},
            dates={"duration_days": 5}, party={"count_status": "exact", "count": 2},
            preferences={"pace": "relaxed", "interests": ["古建"], "dietary_preferences": ["清淡", "不辣"]}),
        candidates=points, candidate_ids=[p.place_id for p in points])


class Routes:
    def __init__(self, reserve=lambda: None, status="ok"):
        self.reserve, self.status = reserve, status
        self.calls = []

    def route(self, query):
        self.reserve()
        self.calls.append(query)
        return route_result(query, "amap", status=self.status,
            **({"duration_seconds": 600, "distance_meters": 500} if self.status == "ok" else {"unknown_reason": "no_route"}))


def test_undated_five_day_plan_keeps_unknowns_and_real_route_provenance():
    session, provider = state(), Routes()
    plan = compute(session, provider, 60)
    assert len(plan.days) == 5 and all(d.date is None for d in plan.days)
    assert len({s.place_id for d in plan.days for s in d.stops if s.category == "attraction"}) == 10
    assert all(len(d.routes) == 5 for d in plan.days)
    assert all(r.duration_seconds == 600 and r.provider == "amap" for d in plan.days for r in d.routes)
    audit = validate(plan, session)
    assert audit.hard_status == "unknown" and not any(i.severity == "blocking" for i in audit.issues)
    assert "出发日期" in plan.missing_requirements and "预算" in plan.missing_requirements
    with pytest.raises(ValidationError):
        ItineraryDraft.model_validate({**plan.model_dump(), "status": "verified"})


def test_subordinate_identity_is_one_visit_but_nearby_distinct_places_stay_separate():
    main = place("main", "恭王府博物馆")
    child = place("child", "恭王府-佛楼东配房")
    neighbour = place("other", "国子监")
    groups = identity_groups([main, child, neighbour])
    assert {p.place_id for p in groups["main"]} == {"main", "child"}
    assert "other" in groups


def test_missing_route_is_unknown_and_does_not_create_schedule_times():
    session = state()
    plan = compute(session, Routes(status="no_route"), 60)
    assert all(r.duration_seconds is None for d in plan.days for r in d.routes)
    assert any(s.start_minute is None for d in plan.days for s in d.stops)
    assert any(i.code == "route_unavailable" for i in validate(plan, session).issues)


def test_validation_detects_omitted_routes_and_does_not_allow_finish():
    session = state()
    plan = compute(session, Routes(), 60)
    plan.days[0].routes = []
    plan.audit = validate(plan, session)
    plan.audit.reviewed = True
    plan.audit.experience_status = "passed"
    from app.schemas.plan import PlanRef
    session.current_plan, session.current_plan_ref = plan, PlanRef(plan_id=plan.plan_id, version=plan.version)
    assert plan.audit.hard_status == "failed"
    action = AGENT_ACTION_ADAPTER.validate_python({"kind": "finish", "action_id": "f", "purpose": "deliver",
        "base_state_version": 1, "plan_ref": session.current_plan_ref})
    with pytest.raises(PlanningError, match="plan_not_deliverable"):
        check_action(action, session)


def test_required_remote_place_retained_and_locked_edit_rejected():
    session = state()
    remote = place("remote", "远郊", lon=117)
    session.candidates.append(remote)
    session.candidate_ids.append("remote")
    from app.schemas.session import Selection
    session.selections = [Selection(selection_id="lock", place_id="remote", decision="lock", evidence={
        "source": "selection", "source_ref": "click", "confirmation": "explicit"})]
    plan = compute(session, Routes(), 60)
    stop = next(s for d in plan.days for s in d.stops if s.place_id == "remote")
    action = AGENT_ACTION_ADAPTER.validate_python({"kind": "edit_plan", "action_id": "e", "purpose": "edit",
        "base_state_version": 1, "plan_ref": {"plan_id": plan.plan_id, "version": 1},
        "changes": [{"kind": "remove_visit", "visit_id": stop.stop_id}]})
    with pytest.raises(PlanningError, match="locked_visit_edit_conflict"):
        edit(plan, action.changes, session)


def test_bounded_route_phase_preserves_partial_result():
    provider = Routes()
    plan = compute(state(), provider, 2)
    assert len(provider.calls) == 2
    assert any(r.unknown_reason == "route_phase_budget_exhausted" for d in plan.days for r in d.routes)


def test_beijing_transit_uses_verified_citycode():
    seen = []
    def handle(req):
        seen.append(req)
        return httpx.Response(200, json={"status": "1", "route": {"transits": []}})
    tx = MapTransport(client=httpx.Client(transport=httpx.MockTransport(handle)))
    provider = AmapProvider(SecretStr("synthetic"), tx)
    result = provider.route(RouteQuery(origin=place("a"), destination=place("b"), mode="transit",
        departure_at=datetime.fromisoformat("2026-10-01T10:00:00+08:00")))
    assert seen[0].url.params["city1"] == "010" and result.status == "no_route"
    tx.close()


def test_real_registry_transport_supports_itinerary_budget_without_network(harness):
    _, runtime, _, _, _ = harness
    runtime.registry.provider_factory = None
    result = runtime.registry._map(lambda provider: provider.transport.max_calls, lambda: None)
    assert result == runtime.settings.itinerary_map_call_limit


def test_supplier_preserves_connections_and_unknown_price_units():
    rows, status = normalize_offers({"status": 0, "data": {"itemList": [{"price": "199", "journeys": [{"segments": [
        {"marketingTransportNo": "X1", "arrCityName": "南京"}, {"marketingTransportNo": "X2", "arrCityName": "北京"}]}]}]}}, "train", "outbound")
    assert status == "observed" and len(rows[0]["segments"]) == 2
    assert rows[0]["currency"] is None and rows[0]["price_scope"] == "unknown"
    calls = []
    offers, statuses = TravelServices(Settings(_env_file=None), runner=lambda args: calls.append(args)).query(
        state().intent_snapshot, ["train", "hotel"], lambda: None)
    assert not calls and not offers and statuses["train"] == "dates_required"


def test_full_plan_led_session_delivers_partial_draft_and_restart_is_truthful(harness):
    client, runtime, model, factory, _ = harness
    class Map(Routes):
        def search_places(self, query, region, limit):
            self.reserve()
            return candidates()[:10]
        def nearby_places(self, coords, query, region, **kwargs):
            self.reserve()
            return [p for p in candidates() if p.category == ("hotel" if query == "酒店" else "restaurant")]
    runtime.registry.provider_factory = Map
    async def complete(system, context, schema):
        if "current_intent" in context:
            return {"patch": {"destination": REGION, "dates": {"duration_days": 5}, "party": {"count_status": "exact", "count": 2},
                "preferences": {"pace": "relaxed", "dietary_preferences": ["不辣"]}}, "unknown_fields": ["dates", "budget"]}
        if "plan" in context:
            return {"status": "unknown", "issues": []}
        s = context["state"]
        base = {"action_id": str(uuid4()), "base_state_version": s["state_version"], "purpose": "规划"}
        if not s["candidates"]:
            return {**base, "kind": "search_candidates", "intent_ref": s["intent_snapshot"]["intent_id"], "region": s["intent_snapshot"]["destination"], "categories": ["attraction"], "limit": 15}
        for category in ("hotel", "restaurant"):
            if not s["coverage"].get(category):
                return {**base, "kind": "rank_nearby", "anchor_refs": [s["candidate_ids"][0]], "category": category, "preference_ref": s["intent_snapshot"]["intent_id"]}
        if not s["current_plan"]:
            return {**base, "kind": "compute_itinerary", "intent_ref": s["intent_snapshot"]["intent_id"], "candidate_ids": s["candidate_ids"], "selection_ref": s["session_id"]}
        if not s["current_plan"]["audit"]:
            return {**base, "kind": "validate_plan", "plan_ref": s["current_plan_ref"], "checks": ["duplicates", "time_windows", "locks", "budget", "evidence", "experience"]}
        return {**base, "kind": "finish", "plan_ref": s["current_plan_ref"]}
    model.complete = complete
    response = client.post("/api/sessions", json={"message": "北京5天2人，不辣，日期预算待定", "task_scope": "itinerary"})
    data = response.json()
    url = "/api/sessions/" + data["state"]["session_id"]
    headers = {"Authorization": "Bearer " + data["access_token"]}
    run(client, url, headers, 0)
    result = wait_state(client, url, headers, "completed")
    draft = result["state"]["current_plan"]
    assert draft["status"] == "partial" and len(draft["days"]) == 5
    assert draft["audit"]["reviewed"] and draft["audit"]["hard_status"] == "unknown"
    assert result["usage"]["map_calls"] <= 80
    restored = SessionStore(factory, runtime.settings).load(data["state"]["session_id"])
    assert restored.state.plan_needs_refresh and restored.state.current_plan is None
    assert restored.state.plan_history and restored.state.search_recipes
    # An already delivered session can be revised; its original arrangement remains in history.
    revised = client.patch(url, headers=headers, json={"request_id": "edit", "base_state_version": result["state"]["state_version"], "intent_patch": {"preferences": {"pace": "balanced"}}})
    assert revised.status_code == 200 and revised.json()["state"]["status"] == "draft"
    assert revised.json()["state"]["intent_snapshot"]["preferences"]["dietary_preferences"] == ["不辣"]
