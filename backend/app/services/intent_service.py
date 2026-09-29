"""Explicit trip input precedes profile defaults; answers only change their gaps."""

from uuid import uuid4

from app.runtime.guards import PlanningError
from app.schemas.common import FieldEvidence
from app.schemas.intent import Preferences, TripIntent


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
    updates = patch.model_dump(mode="json", exclude_none=True)
    if allowed is not None and not set(updates) <= set(allowed):
        raise PlanningError("answer_updates_unrelated_field", 422)
    data = intent.model_dump(mode="json")
    for field, value in updates.items():
        # Structured fields are replaced as one validated unit, avoiding half-date updates.
        data[field] = value
        data["field_evidence"][field] = FieldEvidence(
            source=source, source_ref=source_ref, confirmation="explicit").model_dump(mode="json")
    if source == "answer":
        data["unresolved_questions"] = [g for g in data["unresolved_questions"] if g not in updates]
        data["message_refs"] = list(dict.fromkeys(data["message_refs"] + [source_ref]))
    return TripIntent.model_validate(data)
