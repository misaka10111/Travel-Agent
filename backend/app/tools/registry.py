import asyncio
from uuid import uuid4

from app.providers.maps.amap import AmapProvider
from app.providers.maps.transport import MapTransport
from app.runtime.guards import PlanningError
from app.agents.intake_agent import IntakeAgent
from app.agents.validate_agent import ValidateAgent
from app.planning.engine import compute
from app.planning.edits import edit
from app.planning.ranking import nearby_ranking
from app.planning.validation import validate
from app.schemas.action import ToolResult
from app.schemas.itinerary import PlanManifest
from app.schemas.plan import PlanRef
from app.schemas.session import SearchRecipe


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
        self.intake_agent = IntakeAgent(question_agent.client)
        self.validate_agent = ValidateAgent(question_agent.client)
        self.settings = settings
        self.provider_factory = provider_factory

    def _map(self, operation, reserve):
        if self.provider_factory:
            # Tests substitute only the external service, retaining its budget boundary.
            return operation(self.provider_factory(reserve))
        transport = BudgetMapTransport(reserve, max_calls=max(self.settings.itinerary_map_call_limit, self.settings.planning_map_call_limit),
            timeout_seconds=self.settings.map_request_timeout_seconds, interval_seconds=self.settings.map_request_interval_seconds)
        try:
            return operation(AmapProvider(self.settings.amap_web_service_key, transport))
        finally:
            transport.close()

    async def execute(self, action, snapshot, reserve_model, reserve_map):
        state, result = await self._execute(action, snapshot, reserve_model, reserve_map)
        # A single typed boundary covers every Agent/tool. Preserve flattened
        # payload fields in the Plan observation for backwards-compatible clients.
        payload = {k: v for k, v in result.items() if k not in {"status", "warnings"}}
        if result["status"] in {"paused", "waiting_user", "completed"}:
            payload = {"kind": "state", "state_status": result["status"], "reason": result.get("reason")}
        if payload.get("kind") == "places":
            payload.pop("coverage", None)
        typed = ToolResult(action_id=action.action_id, base_state_version=action.base_state_version,
            status="partial" if result["status"] == "partial" else "success", payload=payload,
            warnings=result.get("warnings", []))
        return state, {**typed.model_dump(mode="json"), **typed.payload.model_dump(mode="json")}

    async def _execute(self, action, snapshot, reserve_model, reserve_map):
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
                # Amap keyword search does not enforce our category filter. A
                # descriptive hotel query can return zero hotel POIs. Try one
                # canonical query before reporting the category unavailable.
                if not found and action.categories == ["hotel"] and "酒店" not in keywords:
                    for place in provider.search_places("酒店", action.region, min(action.limit, 25)):
                        if place.category == "hotel" and place.place_id not in excluded:
                            found[place.place_id] = place
                return list(found.values())[:action.limit]

            places = await asyncio.to_thread(self._map, search, reserve_map)
            # Merge newly retrieved identities; explicit user selections remain independent.
            before_ids = {p.place_id for p in state.candidates}
            merged = {p.place_id: p for p in state.candidates}
            merged.update({p.place_id: p for p in places})
            if len(merged) > 100:
                raise PlanningError("candidate_capacity_reached")
            state.candidates = list(merged.values())
            state.candidate_ids = list(dict.fromkeys(list(merged) + [s.place_id for s in state.selections]))
            state.candidates_need_refresh = len(merged) != len(state.candidate_ids)
            state.coverage = {c: sum(p.category == c and p.coordinates is not None for p in state.candidates)
                for c in ("attraction", "restaurant", "hotel", "transport")}
            new_count = len(set(merged) - before_ids)
            recipe = SearchRecipe(queries=keywords, categories=action.categories, limit=action.limit, new_count=new_count)
            if recipe not in state.search_recipes:
                state.search_recipes = (state.search_recipes + [recipe])[-12:]
            state.candidates_stale = False
            warnings = ["provider_identity_candidates_not_confirmed"]
            if not new_count:
                warnings.append("no_new_candidates_change_search_strategy")
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
        if action.kind == "rank_nearby":
            anchors = [p for p in state.candidates if p.place_id in action.anchor_refs][:3]
            def nearby(provider):
                found = {}
                for anchor in anchors:
                    if anchor.coordinates:
                        for p in provider.nearby_places(anchor.coordinates, "餐厅" if action.category == "restaurant" else "酒店",
                                state.intent_snapshot.destination, radius_meters=2000 if action.category == "restaurant" else 5000, limit=20):
                            if p.category == action.category and p.place_id not in {s.place_id for s in state.selections if s.decision == "exclude"}:
                                found[p.place_id] = p
                return list(found.values())
            places = await asyncio.to_thread(self._map, nearby, reserve_map)
            merged = {p.place_id: p for p in state.candidates}
            kept = []
            for place in places:
                if place.place_id in merged or len(merged) < 150:
                    merged[place.place_id] = place
                    kept.append(place)
            state.candidates = list(merged.values())
            state.candidate_ids = list(dict.fromkeys(list(merged) + [s.place_id for s in state.selections]))
            state.candidates_need_refresh = len(merged) != len(state.candidate_ids)
            state.coverage = {c: sum(p.category == c for p in state.candidates) for c in ("attraction", "restaurant", "hotel", "transport")}
            # Store generic own queries for recovery, not supplier coordinates.
            recipe = SearchRecipe(queries=["餐厅" if action.category == "restaurant" else "酒店"], categories=[action.category], limit=25)
            if recipe not in state.search_recipes:
                state.search_recipes.append(recipe)
            ranking = nearby_ranking(kept, anchors, action.category, state.intent_snapshot)
            return state, {"status": "partial", "kind": "ranking", "items": [
                {"place_id": r.place_id, "score": sum(r.score_components.values()), "explanation": "；".join(r.reasons)} for r in ranking],
                "warnings": ["distance_is_pruning_only; menus_and_inventory_not_confirmed"] +
                    (["candidate_capacity_reached"] if len(kept) < len(places) else [])}
        if action.kind in {"compute_itinerary", "edit_plan"}:
            previous = edit(state.current_plan, action.changes, state) if action.kind == "edit_plan" else None
            if action.kind == "compute_itinerary":
                state.candidates = [p for p in state.candidates if p.place_id in action.candidate_ids]
            remaining = snapshot.usage["map_limit"] - snapshot.usage["map_calls"]
            plan = await asyncio.to_thread(self._map,
                lambda provider: compute(state, provider, min(remaining, self.settings.itinerary_route_call_limit), previous), reserve_map)
            state.current_plan = plan
            state.current_plan_ref = PlanRef(plan_id=plan.plan_id, version=plan.version)
            state.plan_needs_refresh = False
            state.plan_history.append(PlanManifest(plan_id=plan.plan_id, version=plan.version,
                assignments=[[s.place_id for s in d.stops] for d in plan.days], change_reason="edit" if previous else "compute"))
            return state, {"status": "partial", "kind": "plan", "plan": plan.model_dump(mode="json"),
                "warnings": ["draft_has_unverified_requirements"]}
        if action.kind == "validate_plan":
            plan = state.current_plan.model_copy(deep=True)
            plan.audit = validate(plan, state)
            reserve_model()
            plan.audit = await self.validate_agent.review(plan, state.intent_snapshot, state.candidates)
            if plan.audit.hard_status == "passed" and plan.audit.experience_status == "passed" and not plan.audit.issues and not plan.missing_requirements:
                plan.status = "verified"
            else:
                plan.status = "partial"
            state.current_plan = plan
            return state, {"status": "partial" if plan.audit.issues else "success", "kind": "audit", "report": plan.audit.model_dump(mode="json"),
                "warnings": ["unknowns_or_conflicts_remain"] if plan.audit.issues else []}
        if action.kind == "finish":
            state.status = "completed"
            state.attention_reason = "draft_delivered_with_unknowns" if state.current_plan.status != "verified" else None
            return state, {"status": "completed", "reason": state.attention_reason}
        if action.kind in {"query_travel", "search_evidence"}:
            from app.providers.travel import TravelServices
            service = TravelServices(self.settings)
            if action.kind == "query_travel":
                # Supplier calls share the model/service budget, never create an unbounded third allowance.
                offers, statuses = await asyncio.to_thread(service.query, state.intent_snapshot, action.categories, reserve_model)
                state.travel_offers = [o for o in state.travel_offers if o.get("category") not in action.categories] + offers
                state.supplier_status.update(statuses)
                if offers:
                    from app.planning.transfers import match_offers
                    state.candidates = await asyncio.to_thread(self._map,
                        lambda provider: match_offers(offers, state.candidates, provider, state.intent_snapshot.destination), reserve_map)
                    state.candidate_ids = list(dict.fromkeys([p.place_id for p in state.candidates] + [s.place_id for s in state.selections]))
                if state.current_plan:
                    state.plan_needs_refresh = True
                return state, {"status": "partial", "kind": "state", "state_status": "supplier_observed", "warnings": list(statuses.values()) or ["no_inventory_verified"]}
            places = [p for p in state.candidates if p.place_id in action.place_ids]
            enriched = await asyncio.to_thread(service.evidence, places, action.topic, reserve_model)
            found = {p.place_id: p for p in enriched}
            state.candidates = [found.get(p.place_id, p) for p in state.candidates]
            return state, {"status": "partial", "kind": "places", "places": [p.model_dump(mode="json") for p in enriched], "warnings": ["web_evidence_requires_confirmation"]}
        raise PlanningError("capability_unavailable")
