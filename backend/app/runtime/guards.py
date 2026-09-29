from app.schemas.planning_api import IntentPatch

AVAILABLE_ACTIONS = ["ask_user", "search_candidates", "get_place_details", "pause"]
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
