from app.schemas.planning_api import IntentPatch

AVAILABLE_ACTIONS = ["ask_user", "search_candidates", "get_place_details", "rank_nearby", "search_evidence", "query_travel", "compute_itinerary", "validate_plan", "edit_plan", "finish", "pause"]
GAP_FIELDS = set(IntentPatch.model_fields)


class PlanningError(Exception):
    def __init__(self, code: str, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def check_action(action, state):
    if action.base_state_version != state.state_version:
        raise PlanningError("stale_action")
    if action.kind not in AVAILABLE_ACTIONS:
        # No route solver / independently verified plan exists in P1.
        raise PlanningError("capability_unavailable_" + action.kind)
    if not set(action.scope.place_ids) <= set(state.candidate_ids):
        raise PlanningError("unknown_scope_place")
    dates = state.intent_snapshot.dates
    if action.scope.dates and (dates.start_date is None or dates.end_date is None or
            any(day < dates.start_date or day > dates.end_date for day in action.scope.dates)):
        raise PlanningError("scope_outside_trip_dates")
    if action.kind == "ask_user" and not set(action.gap_ids) <= GAP_FIELDS:
        raise PlanningError("unknown_question_gap")
    if action.kind == "ask_user" and set(action.gap_ids) & set(state.intake_state.unknown_fields + state.intake_state.declined_fields):
        raise PlanningError("question_already_unknown_or_declined")
    if action.kind == "search_candidates":
        if action.intent_ref != state.intent_snapshot.intent_id:
            raise PlanningError("wrong_intent_ref")
        if action.region != state.intent_snapshot.destination:
            raise PlanningError("search_region_mismatch")
        if action.region.country_code != "CN":
            raise PlanningError("overseas_deferred")
        if not set(action.filters.exclude_place_ids) <= set(state.candidate_ids):
            raise PlanningError("unknown_excluded_place")
    if action.kind == "get_place_details":
        place = next((p for p in state.candidates if p.place_id == action.place_id), None)
        if place is None or action.provider_ref not in place.provider_refs:
            raise PlanningError("place_requires_refresh_or_wrong_provider_ref")
    if action.kind == "compute_itinerary":
        if action.intent_ref != state.intent_snapshot.intent_id:
            raise PlanningError("wrong_intent_ref")
        if action.selection_ref != state.session_id:
            raise PlanningError("wrong_selection_ref")
        if not set(action.candidate_ids) <= {p.place_id for p in state.candidates}:
            raise PlanningError("candidates_require_refresh")
        required = {s.place_id for s in state.selections if s.decision in {"lock", "include"}}
        if not required <= set(action.candidate_ids):
            raise PlanningError("locked_places_omitted")
    if action.kind == "rank_nearby":
        if action.preference_ref != state.intent_snapshot.intent_id or not set(action.anchor_refs) <= {p.place_id for p in state.candidates}:
            raise PlanningError("invalid_nearby_refs")
    if action.kind in {"validate_plan", "edit_plan", "finish"}:
        if not state.current_plan or state.plan_needs_refresh or action.plan_ref != state.current_plan_ref:
            raise PlanningError("plan_requires_refresh_or_wrong_ref")
    if action.kind == "finish":
        audit = state.current_plan.audit
        if not audit or not audit.reviewed or audit.plan_version != state.current_plan.version or audit.hard_status == "failed" or audit.experience_status == "failed" or any(i.severity == "blocking" for i in audit.issues):
            raise PlanningError("plan_not_deliverable")
    if action.kind == "search_evidence" and not set(action.place_ids) <= {p.place_id for p in state.candidates}:
        raise PlanningError("evidence_place_requires_refresh")
