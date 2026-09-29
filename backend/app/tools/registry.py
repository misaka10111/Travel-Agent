import asyncio
from uuid import uuid4

from app.providers.maps.amap import AmapProvider
from app.providers.maps.transport import MapTransport
from app.runtime.guards import PlanningError


class BudgetMapTransport(MapTransport):
    def __init__(self, reserve, **kwargs):
        super().__init__(**kwargs)
        self.reserve = reserve

    def request(self, method, url, **kwargs):
        self.reserve()  # Failed paid calls also consume the durable per-session budget.
        return super().request(method, url, **kwargs)


class ToolRegistry:
    def __init__(self, question_agent, settings, provider_factory=None):
        self.question_agent = question_agent
        self.settings = settings
        self.provider_factory = provider_factory

    def _map(self, operation, reserve):
        if self.provider_factory:
            # Tests substitute only the external service, retaining its budget boundary.
            return operation(self.provider_factory(reserve))
        transport = BudgetMapTransport(reserve, max_calls=self.settings.planning_map_call_limit,
            timeout_seconds=self.settings.map_request_timeout_seconds)
        try:
            return operation(AmapProvider(self.settings.amap_web_service_key, transport))
        finally:
            transport.close()

    async def execute(self, action, snapshot, reserve_model, reserve_map):
        state = snapshot.state.model_copy(deep=True)
        if action.kind == "pause":
            state.status, state.attention_reason = "needs_attention", "plan_paused"
            state.agent_message = action.reason
            return state, {"status": "paused", "reason": action.reason}
        if action.kind == "ask_user":
            reserve_model()
            question = await self.question_agent.ask(action, snapshot, str(uuid4()))
            state.pending_questions = [question]
            state.intent_snapshot.unresolved_questions = list(dict.fromkeys(
                state.intent_snapshot.unresolved_questions + action.gap_ids))
            state.status = "waiting_user"
            return state, {"status": "waiting_user", "question_id": question.question_id}
        if action.kind == "search_candidates":
            keywords = action.filters.keywords
            if not keywords:
                labels = {"attraction": "旅游景点", "hotel": "酒店", "restaurant": "餐厅", "transport": "交通枢纽"}
                keywords = [labels[c] for c in action.categories]
            keywords = list(dict.fromkeys(keywords))[:3]

            def search(provider):
                found = {}
                excluded = set(action.filters.exclude_place_ids)
                excluded.update(s.place_id for s in state.selections if s.decision == "exclude")
                excluded.update(c.place_id for c in state.intent_snapshot.constraints if c.kind == "exclude" and c.strength == "hard")
                for keyword in keywords:
                    for place in provider.search_places(keyword, action.region, min(action.limit, 25)):
                        if place.category in action.categories and place.place_id not in excluded:
                            found[place.place_id] = place
                return list(found.values())[:action.limit]

            places = await asyncio.to_thread(self._map, search, reserve_map)
            # Merge newly retrieved identities; explicit user selections remain independent.
            merged = {p.place_id: p for p in state.candidates}
            merged.update({p.place_id: p for p in places})
            if len(merged) > 100:
                raise PlanningError("candidate_capacity_reached")
            state.candidates = list(merged.values())
            state.candidate_ids = list(dict.fromkeys(list(merged) + [s.place_id for s in state.selections]))
            state.candidates_need_refresh = len(merged) != len(state.candidate_ids)
            warnings = ["provider_identity_candidates_not_confirmed", "ranking_and_route_solver_require_P2"]
            if action.filters.max_price is not None or action.filters.accessible is not None:
                warnings.append("price_accessibility_filters_not_verified_by_provider")
            return state, {"status": "success" if places else "partial", "kind": "places",
                "places": [p.model_dump(mode="json") for p in places], "warnings": warnings}
        if action.kind == "get_place_details":
            place = await asyncio.to_thread(self._map,
                lambda provider: provider.get_place(action.provider_ref.provider_place_id, state.intent_snapshot.destination), reserve_map)
            if place.place_id != action.place_id:
                raise PlanningError("place_identity_mismatch")
            state.candidates = [place if p.place_id == place.place_id else p for p in state.candidates]
            return state, {"status": "success", "kind": "places", "places": [place.model_dump(mode="json")],
                "warnings": ["requested_fields_may_be_unknown"]}
        raise PlanningError("capability_unavailable")
