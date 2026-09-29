"""Meaningful failure boundaries, not live API tests."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.action import AGENT_ACTION_ADAPTER, ToolResult
from app.schemas.intent import Party, TripDates, TripIntent
from app.schemas.place import Coordinates, Place
from app.schemas.plan import PlanVersion, RouteLeg, Stay, Visit
from app.schemas.session import Answer, Question, SessionState


def fixture_intent():
    return json.loads((Path(__file__).parent / "fixtures/p0_intent.json").read_text(encoding="utf-8"))


def route(**updates):
    return {
        "leg_id": "l1", "from_place_id": "p1", "to_place_id": "p2",
        "mode": "transit", "provider": "amap", "status": "no_route",
        "requested_departure_at": "2026-09-29T09:00:00+08:00",
        "evidence_ref": "fixture-route", "unknown_reason": "no route",
        **updates,
    }


def test_draft_never_invents_dates_currency_or_exact_count():
    intent = TripIntent.model_validate(fixture_intent())
    assert intent.dates.start_date is None
    assert intent.party.count is None and intent.party.count_min == 6
    assert intent.budget.money is None
    assert TripIntent.model_validate_json(intent.model_dump_json()) == intent


@pytest.mark.parametrize("payload", [
    {"start_date": "2026-10-02", "end_date": "2026-10-01"},
    {"start_date": "2026-10-01", "end_date": "2026-10-05", "duration_days": 6},
    {"timezone": "Mars/Unknown"},
])
def test_date_conflicts_rejected(payload):
    with pytest.raises(ValidationError):
        TripDates.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"count_status": "lower_bound", "count": 6},
    {"count_status": "unknown", "count": 2},
    {"count_status": "exact", "count": 0},
])
def test_party_bounds_not_exact_counts(payload):
    with pytest.raises(ValidationError):
        Party.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"longitude": 121, "latitude": 31},
    {"longitude": 181, "latitude": 31, "crs": "GCJ02"},
    {"longitude": 121, "latitude": float("nan"), "crs": "WGS84"},
])
def test_coordinates_require_known_units_and_crs(payload):
    with pytest.raises(ValidationError):
        Coordinates.model_validate(payload)


def test_place_unknowns_are_explicit_and_branches_keep_identity():
    base = {"name": "同名餐馆", "category": "restaurant", "region": {"label": "上海"}}
    with pytest.raises(ValidationError):
        Place.model_validate({**base, "place_id": "branch-a"})
    a = Place.model_validate({**base, "place_id": "branch-a", "coordinate_unknown_reason": "unresolved"})
    b = Place.model_validate({**base, "place_id": "branch-b", "coordinate_unknown_reason": "unresolved"})
    assert a.place_id != b.place_id


@pytest.mark.parametrize("updates", [
    {"duration_seconds": 0},
    {"requested_departure_at": "2026-09-29T09:00:00"},
    {"status": "ok", "unknown_reason": None},
    {"status": "ok", "unknown_reason": None, "duration_seconds": -1, "distance_meters": 20},
])
def test_route_cannot_fake_success_or_naive_time(updates):
    with pytest.raises(ValidationError):
        RouteLeg.model_validate(route(**updates))


def test_no_route_keeps_null_estimates():
    leg = RouteLeg.model_validate(route())
    assert leg.duration_seconds is None and leg.distance_meters is None


def test_hotel_requires_real_overnight_interval():
    with pytest.raises(ValidationError):
        Stay(stay_id="stay", hotel_place_id="hotel", check_in="2026-10-05", check_out="2026-10-05")


def test_activity_does_not_require_fake_place_id():
    visit = Visit(visit_id="rest", kind="activity", activity_label="休息", date="2026-10-01")
    assert visit.place_id is None


def test_unknown_audit_cannot_mark_plan_verified():
    with pytest.raises(ValidationError):
        PlanVersion(plan_id="p", version=1, base_state_version=0,
                    intent_snapshot_ref="i", status="verified", days=[{"date": "2026-10-01"}])


def test_action_union_rejects_arbitrary_tools_and_finish_override():
    base = {"action_id": "a", "base_state_version": 1, "purpose": "交付"}
    action = AGENT_ACTION_ADAPTER.validate_python({**base, "kind": "finish", "plan_ref": {"plan_id": "p", "version": 1}})
    assert action.kind == "finish"
    for payload in (
        {**base, "kind": "run_shell", "command": "something"},
        {**base, "kind": "finish", "plan_ref": {"plan_id": "p", "version": 1}, "passed": True},
        {**base, "kind": "compute_itinerary", "intent_ref": "i", "candidate_ids": [], "selection_ref": "s"},
    ):
        with pytest.raises(ValidationError):
            AGENT_ACTION_ADAPTER.validate_python(payload)


def test_result_cannot_claim_success_with_error():
    with pytest.raises(ValidationError):
        ToolResult(action_id="a", base_state_version=0, status="success",
                   error={"code": "timeout", "message": "timeout"})


def test_question_binding_rejects_stale_answer_and_unknown_option():
    question = Question(question_id="q", state_version=2, gap_ids=["pace"], prompt="节奏？", mode="single",
                        options=[{"option_id": "relaxed", "label": "轻松"}, {"option_id": "busy", "label": "紧凑"}])
    Answer(request_id="r", question_id="q", state_version=2, option_ids=["relaxed"]).check_question(question)
    for version, options in ((1, ["relaxed"]), (2, ["other"]), (2, ["relaxed", "busy"])):
        with pytest.raises(ValueError):
            Answer(request_id="r", question_id="q", state_version=version, option_ids=options).check_question(question)


def test_waiting_state_requires_persistable_question():
    with pytest.raises(ValidationError):
        SessionState(session_id="s", owner_ref="anonymous", state_version=0,
                     intent_snapshot=fixture_intent(), status="waiting_user")
