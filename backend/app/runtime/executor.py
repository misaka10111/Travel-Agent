"""A Plan-led action loop. No business phase is selected by this runtime."""

import asyncio

from pydantic import ValidationError

from app.providers.maps.base import MapError
from app.runtime.guards import PlanningError, check_action
from app.services.intent_service import apply_patch


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
        while True:
            snapshot = self.store.load(session_id)
            if snapshot.lease_id != lease_id or snapshot.state.status != "running":
                return
            reserve_model = lambda: self.store.reserve(session_id, "model_calls", lease_id=lease_id)
            reserve_map = lambda: self.store.reserve(session_id, "map_calls", lease_id=lease_id)
            # Answers are durable commands before interpretation, so retrying HTTP cannot double-submit.
            queued = next((m for m in snapshot.messages if m.get("kind") == "answer" and not m.get("processed")), None)
            if queued:
                reserve_model()
                patch = await self.registry.question_agent.interpret(queued, snapshot.state.intent_snapshot)
                state = snapshot.state
                state.intent_snapshot = apply_patch(state.intent_snapshot, patch, source="answer",
                    source_ref=queued["answer"]["request_id"], allowed=queued["question"]["gap_ids"])
                queued["processed"] = True
                self.store.save(state, expected=state.state_version, lease_id=lease_id,
                    kind="answer_interpreted", messages=snapshot.messages)
                continue
            self.store.reserve(session_id, "steps", lease_id=lease_id)
            reserve_model()
            try:
                action = await self.plan.decide(snapshot, observation)
                check_action(action, snapshot.state)
                state, observation = await self.registry.execute(action, snapshot, reserve_model, reserve_map)
            except (ValidationError, PlanningError) as exc:
                repairable = isinstance(exc, ValidationError) or exc.code in {
                    "model_invalid_json", "model_empty_content", "model_output_truncated"}
                if not repairable:
                    raise
                if not repairs_left:
                    raise
                repairs_left -= 1
                # Give Plan one bounded repair opportunity, with paths/types only.
                # No supplier payload, raw model output or private reasoning is persisted.
                errors = [
                    {"path": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors(include_input=False, include_url=False)[:8]] if isinstance(exc, ValidationError) else []
                observation = {"status": "error", "code": "invalid_agent_contract" if isinstance(exc, ValidationError) else exc.code,
                    "contract_errors": errors}
                self.store.diagnostic(session_id, lease_id, observation)
                continue
            # Event data is metadata only. Never save supplier objects or model transcripts.
            self.store.save(state, expected=snapshot.state.state_version, lease_id=lease_id, kind="action_applied",
                event_data={"action_kind": action.kind, "action_id": action.action_id, "status": observation["status"]})
            if state.status != "running":
                return

    async def run(self, session_id, lease_id):
        heartbeat = asyncio.create_task(self._heartbeat(session_id, lease_id))
        try:
            await asyncio.wait_for(self._loop(session_id, lease_id), timeout=self.settings.planning_run_timeout_seconds)
        except asyncio.CancelledError:
            self._stop(session_id, lease_id, "server_shutdown_resume_available")
            raise
        except TimeoutError:
            self._stop(session_id, lease_id, "run_timeout")
        except (PlanningError, MapError) as exc:
            self._stop(session_id, lease_id, exc.code)
        except ValidationError:
            self._stop(session_id, lease_id, "invalid_agent_contract")
        except Exception:
            self._stop(session_id, lease_id, "runtime_internal_error")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def shutdown(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
