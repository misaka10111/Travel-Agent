"""Input preservation and intake are tested through their real contracts and runtime."""

import pytest
from pydantic import ValidationError

from app.schemas.intent import TripIntent
from app.schemas.planning_api import IntentPatch
from app.services.intent_service import apply_patch
from tests.test_p1_runtime import harness, create, run, wait_state


def test_preference_edit_preserves_dietary_interests_and_explicit_clear():
    intent = TripIntent(intent_id="i", destination={"label": "北京"}, preferences={
        "interests": ["古代建筑"], "dietary_preferences": ["不辣"], "pace": "busy"})
    updated = apply_patch(intent, IntentPatch(preferences={"pace": "relaxed"}), source="form", source_ref="edit")
    assert updated.preferences.interests == ["古代建筑"]
    assert updated.preferences.dietary_preferences == ["不辣"]
    assert updated.preferences.pace == "relaxed"
    cleared = apply_patch(updated, IntentPatch(preferences={"pace": None, "interests": []}), source="form", source_ref="clear")
    assert cleared.preferences.pace is None and not cleared.preferences.interests
    assert cleared.preferences.dietary_preferences == ["不辣"]


def test_atomic_date_change_rejects_conflicting_duration():
    with pytest.raises(ValidationError):
        IntentPatch(dates={"start_date": "2026-10-01", "end_date": "2026-10-05", "duration_days": 3})


def test_raw_message_intake_runs_with_one_schema_repair(harness):
    client, runtime, model, _, _ = harness
    original = model.complete
    attempts = []

    async def complete(system, context, schema):
        if "current_intent" in context:
            attempts.append(context)
            if len(attempts) == 1:
                return {"patch": {"party": {"count": 2}}}
            return {"patch": {"destination": {"label": "北京", "country_code": "CN"},
                "origin": {"label": "上海", "country_code": "CN"},
                "dates": {"duration_days": 5}, "party": {"count_status": "exact", "count": 2},
                "preferences": {"interests": ["古代建筑"], "dietary_preferences": ["不辣", "清淡"]}},
                "unknown_fields": ["dates", "budget"]}
        return await original(system, context, schema)

    model.complete = complete
    response = client.post("/api/sessions", json={"message": "上海到北京5天，2人，喜欢古代建筑，不辣清淡，日期预算待定"})
    assert response.status_code == 201
    created = response.json()
    url = "/api/sessions/" + created["state"]["session_id"]
    headers = {"Authorization": "Bearer " + created["access_token"]}
    run(client, url, headers, 0)
    state = wait_state(client, url, headers)["state"]
    assert len(attempts) == 2 and attempts[-1]["repair"]["contract_errors"]
    assert state["intent_snapshot"]["dates"]["start_date"] is None
    assert state["intent_snapshot"]["dates"]["duration_days"] == 5
    assert state["intake_state"]["unknown_fields"] == ["dates", "budget"]
    assert state["intent_snapshot"]["preferences"]["dietary_preferences"] == ["不辣", "清淡"]


def test_queued_question_answer_receives_bounded_repair(harness):
    client, _, model, _, _ = harness
    original = model.complete
    attempts = []

    async def complete(system, context, schema):
        if "answer" in context:
            attempts.append(context)
            if len(attempts) == 1:
                return {"party": {"count": 2}}
        return await original(system, context, schema)

    model.complete = complete
    url, headers, _ = create(client, known=False)
    run(client, url, headers, 0)
    state = wait_state(client, url, headers, "waiting_user")["state"]
    q = state["pending_questions"][0]
    answer = client.post(url + "/answers", headers=headers, json={"answer": {"request_id": "a",
        "question_id": q["question_id"], "state_version": state["state_version"], "text": "两人"}}).json()
    run(client, url, headers, answer["state"]["state_version"])
    result = wait_state(client, url, headers)
    assert len(attempts) == 2 and attempts[1]["repair"]
    assert result["state"]["intent_snapshot"]["party"]["count"] == 2
