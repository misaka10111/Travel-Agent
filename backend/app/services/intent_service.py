"""Explicit trip input precedes profile defaults; answers only change their gaps."""

from uuid import uuid4

from app.runtime.guards import PlanningError
from app.schemas.common import FieldEvidence
from app.schemas.intent import Budget, Party, Preferences, TripDates, TripIntent


def with_profile(intent, profile):
    data = intent.model_dump(mode="json")
    ref = "profile:" + str(uuid4())
    data["profile_snapshot_ref"] = ref
    preferences = data["preferences"]
    defaults = profile.preferences.model_dump(mode="json")
    defaults["comfort_tags"] = list(dict.fromkeys(defaults["comfort_tags"] + profile.travel_style))
    for field in Preferences.model_fields:
        # An explicitly submitted empty value still outranks a long-term default.
        if field not in intent.preferences.model_fields_set and defaults[field]:
            preferences[field] = defaults[field]
            data["field_evidence"]["preferences." + field] = FieldEvidence(
                source="profile", source_ref=ref, confirmation="inferred").model_dump(mode="json")
    return TripIntent.model_validate(data)


def apply_patch(intent, patch, *, source, source_ref, allowed=None):
    # Omitted means untouched; explicit null means reset. Nested preference fields
    # are independent, whereas dates/party/budget remain validated atomic units.
    updates = patch.model_dump(mode="json", exclude_unset=True)
    if allowed is not None and not set(updates) <= set(allowed):
        raise PlanningError("answer_updates_unrelated_field", 422)
    data = intent.model_dump(mode="json")
    for field, value in updates.items():
        if field == "destination" and value is None:
            raise PlanningError("destination_cannot_be_cleared", 422)
        if field == "preferences":
            defaults = Preferences().model_dump(mode="json")
            if value is None:
                data[field] = defaults
            else:
                for name, item in value.items():
                    data[field][name] = defaults[name] if item is None else item
                    data["field_evidence"][f"preferences.{name}"] = FieldEvidence(
                        source=source, source_ref=source_ref, confirmation="explicit").model_dump(mode="json")
        elif field in {"dates", "party", "budget"}:
            defaults = {"dates": TripDates, "party": Party, "budget": Budget}
            data[field] = defaults[field]().model_dump(mode="json") if value is None else value
        else:
            data[field] = "" if field == "notes" and value is None else ([] if field == "constraints" and value is None else value)
        data["field_evidence"][field] = FieldEvidence(
            source=source, source_ref=source_ref, confirmation="explicit").model_dump(mode="json")
    if source == "answer":
        data["unresolved_questions"] = [g for g in data["unresolved_questions"] if g not in updates]
        data["message_refs"] = list(dict.fromkeys(data["message_refs"] + [source_ref]))
    return TripIntent.model_validate(data)


def invalidate(state, previous):
    """Invalidate dependent results without dropping user locks or inventing identities."""
    current = state.intent_snapshot
    if previous == current:
        return
    state.current_plan_ref = None
    state.current_plan = None
    state.plan_needs_refresh = False
    state.coverage = {}
    if previous.destination != current.destination:
        if any(s.decision == "lock" for s in state.selections):
            raise PlanningError("destination_change_conflicts_with_locked_places", 422)
        state.candidates, state.candidate_ids, state.selections = [], [], []
        state.search_recipes = []
        state.candidates_need_refresh = False
    state.candidates_stale = previous.preferences != current.preferences or previous.dates != current.dates
