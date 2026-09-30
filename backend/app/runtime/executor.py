"""A Plan-led action loop. No business phase is selected by this runtime."""

import asyncio

from pydantic import ValidationError

from app.providers.maps.base import MapError
from app.runtime.guards import PlanningError, check_action
from app.runtime.store import fingerprint
from app.services.intent_service import apply_patch, invalidate


class PlanningRuntime:
    def __init__(self, store, plan_agent, registry, settings):
        self.store, self.plan, self.registry, self.settings = store, plan_agent, registry, settings
        self.tasks = set()

    def start(self, session_id, lease_id):
        task = asyncio.create_task(self.run(session_id, lease_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _heartbeat(self, session_id, lease_id):
        while True:
            await asyncio.sleep(5)
            if not self.store.heartbeat(session_id, lease_id):
                return

    def _stop(self, session_id, lease_id, reason):
        snapshot = self.store.load(session_id)
        if snapshot.lease_id != lease_id or snapshot.state.status != "running":
            return
        state = snapshot.state
        state.status, state.attention_reason = "needs_attention", reason
        state.agent_message = None
        try:
            self.store.save(state, expected=state.state_version, lease_id=lease_id, kind="stopped", event_data={"code": reason})
        except PlanningError:
            pass  # Edit, cancellation or lease loss already superseded this task.

    async def _loop(self, session_id, lease_id):
        observation = None
        repairs_left = 1
        repeated_errors = {}
        blocked_actions = set()
        blocked_attempts = 0
        business_repairs = 2
        while True:
            snapshot = self.store.load(session_id)
            if snapshot.lease_id != lease_id or snapshot.state.status != "running":
                return
            reserve_model = lambda: self.store.reserve(session_id, "model_calls", lease_id=lease_id)
            reserve_map = lambda: self.store.reserve(session_id, "map_calls", lease_id=lease_id)
            # Answers are durable commands before interpretation, so retrying HTTP cannot double-submit.
            queued = next((m for m in snapshot.messages if m.get("kind") in {"answer", "intake"} and not m.get("processed")), None)
            if queued:
                repair = None
                for attempt in range(2):
                    reserve_model()
                    try:
                        if queued["kind"] == "intake":
                            result = await self.registry.intake_agent.interpret(queued["text"], snapshot.state.intent_snapshot, repair)
                            patch = result.patch
                            source_ref = queued["message_id"]
                            allowed = None
                        else:
                            patch = await self.registry.question_agent.interpret(queued, snapshot.state.intent_snapshot, repair)
                            source_ref = queued["answer"]["request_id"]
                            allowed = queued["question"]["gap_ids"]
                        updated = apply_patch(snapshot.state.intent_snapshot, patch, source="message" if queued["kind"] == "intake" else "answer",
                            source_ref=source_ref, allowed=allowed)
                        break
                    except (ValidationError, PlanningError) as exc:
                        if attempt or not self._repairable(exc):
                            raise
                        repair = self._error_observation(exc)
                        self.store.diagnostic(session_id, lease_id, {**repair, "stage": queued["kind"]})
                state = snapshot.state
                previous = state.intent_snapshot
                state.intent_snapshot = updated
                invalidate(state, previous)
                if "flight" in updated.preferences.intercity_modes and not self.settings.travel_supplier_enabled:
                    state.supplier_status["flight"] = "disabled_pending_account_validation"
                if queued["kind"] == "intake":
                    valid = {"dates", "budget", "party", "origin", "destination", "preferences", "constraints"}
                    state.intake_state.unknown_fields = list(dict.fromkeys(state.intake_state.unknown_fields + [f for f in result.unknown_fields if f in valid]))
                    state.intake_state.declined_fields = list(dict.fromkeys(state.intake_state.declined_fields + [f for f in result.declined_fields if f in valid]))
                queued["processed"] = True
                self.store.save(state, expected=state.state_version, lease_id=lease_id,
                    kind="answer_interpreted", messages=snapshot.messages)
                continue
            self.store.reserve(session_id, "steps", lease_id=lease_id)
            reserve_model()
            try:
                action = await self.plan.decide(snapshot, observation)
                check_action(action, snapshot.state)
                signature = fingerprint(action.kind, action.model_dump(mode="json", exclude={"action_id", "base_state_version", "purpose"}))
                hotel_search_exhausted = (action.kind == "search_candidates" and action.categories == ["hotel"]
                    and snapshot.state.coverage.get("hotel", 0) == 0
                    and any(r.categories == ["hotel"] and r.new_count == 0 for r in snapshot.state.search_recipes))
                if signature in blocked_actions or hotel_search_exhausted:
                    blocked_attempts += 1
                    observation = {"status": "error", "code": "search_already_tried_without_new_candidates",
                        "action_kind": action.kind, "allowed_alternatives": ["rank_nearby", "compute_itinerary", "pause"]}
                    self.store.diagnostic(session_id, lease_id, observation)
                    if blocked_attempts >= 2:
                        state = snapshot.state
                        state.status, state.attention_reason = "needs_attention", "search_stalled_no_new_candidates"
                        state.agent_message = "检索没有新增地点，请调整条件或用附近搜索；已停止重复调用。"
                        self.store.save(state, expected=state.state_version, lease_id=lease_id,
                            kind="stopped", event_data={"code": state.attention_reason})
                        return
                    continue
                if signature in repeated_errors:
                    raise PlanningError(repeated_errors[signature])
                state, observation = await self.registry.execute(action, snapshot, reserve_model, reserve_map)
                progress = (tuple(state.candidate_ids), state.current_plan_ref.model_dump_json() if state.current_plan_ref else None)
                if action.kind in {"search_candidates", "rank_nearby", "get_place_details"}:
                    if progress == (tuple(snapshot.state.candidate_ids), snapshot.state.current_plan_ref.model_dump_json() if snapshot.state.current_plan_ref else None):
                        blocked_actions.add(signature)
                        observation["warnings"] = list(dict.fromkeys(observation.get("warnings", []) + ["no_new_candidates_change_strategy"]))
            except MapError as exc:
                signature = fingerprint(action.kind, action.model_dump(mode="json", exclude={"action_id", "base_state_version", "purpose"}))
                if signature in repeated_errors or exc.code in {"permission_denied", "missing_credentials"}:
                    raise
                repeated_errors[signature] = exc.code
                observation = {"status": "error", "code": exc.code, "retryable": exc.retryable, "action_kind": action.kind}
                self.store.diagnostic(session_id, lease_id, observation)
                continue
            except (ValidationError, PlanningError) as exc:
                if isinstance(exc, PlanningError) and exc.code in {
                    "duration_required_or_exceeds_30_days", "attraction_candidates_required", "locked_places_require_refresh",
                    "locked_visit_edit_conflict", "locked_hotel_edit_conflict", "plan_not_deliverable",
                    "conflicting_parent_child_visit_dates", "multiple_locked_hotels_require_explicit_stay_assignment",
                    "question_already_unknown_or_declined"} and business_repairs:
                    business_repairs -= 1
                    observation = {"status": "error", "code": exc.code, "action_kind": action.kind}
                    self.store.diagnostic(session_id, lease_id, observation)
                    continue
                repairable = self._repairable(exc)
                if not repairable:
                    raise
                if not repairs_left:
                    raise
                repairs_left -= 1
                # Give Plan one bounded repair opportunity, with paths/types only.
                # No supplier payload, raw model output or private reasoning is persisted.
                observation = self._error_observation(exc)
                self.store.diagnostic(session_id, lease_id, observation)
                continue
            # Event data is metadata only. Never save supplier objects or model transcripts.
            self.store.save(state, expected=snapshot.state.state_version, lease_id=lease_id, kind="action_applied",
                event_data={"action_kind": action.kind, "action_id": action.action_id, "status": observation["status"]})
            if state.status != "running":
                return

    @staticmethod
    def _repairable(exc):
        return isinstance(exc, ValidationError) or exc.code in {
            "model_invalid_json", "model_empty_content", "model_output_truncated"}

    @staticmethod
    def _error_observation(exc):
        errors = [{"path": list(e["loc"]), "type": e["type"]}
            for e in exc.errors(include_input=False, include_url=False)[:8]] if isinstance(exc, ValidationError) else []
        return {"status": "error", "code": "invalid_agent_contract" if isinstance(exc, ValidationError) else exc.code,
            "contract_errors": errors}

    async def run(self, session_id, lease_id):
        heartbeat = asyncio.create_task(self._heartbeat(session_id, lease_id))
        try:
            snapshot = self.store.load(session_id)
            timeout = self.settings.itinerary_run_timeout_seconds if snapshot.state.task_scope == "itinerary" else self.settings.planning_run_timeout_seconds
            await asyncio.wait_for(self._loop(session_id, lease_id), timeout=timeout)
        except asyncio.CancelledError:
            self._stop(session_id, lease_id, "server_shutdown_resume_available")
            raise
        except TimeoutError:
            self._stop(session_id, lease_id, "run_timeout")
        except (PlanningError, MapError) as exc:
            self._stop(session_id, lease_id, exc.code)
        except ValidationError:
            self._stop(session_id, lease_id, "invalid_agent_contract")
        except Exception as exc:
            self.store.diagnostic(session_id, lease_id, {"code": "runtime_internal_error", "error_type": type(exc).__name__})
            self._stop(session_id, lease_id, "runtime_internal_error")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def shutdown(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
