"""SQL CAS boundaries, finite budgets, leases and safe durable state."""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select, update

from app.models.planning import PlanningCommand, PlanningEvent, PlanningSession
from app.runtime.guards import AVAILABLE_ACTIONS, PlanningError
from app.schemas.planning_api import CreateSession, ProfileSnapshot, SessionView
from app.schemas.session import CallBudget, SessionState
from app.schemas.intent import TripIntent
from app.services.intent_service import with_profile


@dataclass
class Snapshot:
    state: SessionState
    profile: ProfileSnapshot
    messages: list
    usage: dict
    lease_id: str | None
    lease_until: float


def fingerprint(kind, payload):
    return hashlib.sha256(json.dumps([kind, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SessionStore:
    def __init__(self, session_factory, settings):
        self.factory = session_factory
        self.settings = settings
        self._places = {}  # (version, supplier objects), never persisted

    @staticmethod
    def _durable(state):
        data = state.model_dump(mode="json")
        data["candidates"] = []
        data["agent_message"] = None  # Model summaries can repeat supplier content; keep them volatile too.
        data["current_plan"] = None
        data["travel_offers"] = []
        data["plan_needs_refresh"] = bool(data["current_plan_ref"])
        data["candidates_need_refresh"] = bool(data["candidate_ids"])
        return data

    def create(self, body):
        session_id, token = str(uuid4()), secrets.token_urlsafe(32)
        intent = body.intent or TripIntent(intent_id=str(uuid4()),
            destination={"label": "待确认", "identity_status": "unresolved"})
        map_limit = self.settings.itinerary_map_call_limit if body.task_scope == "itinerary" else self.settings.planning_map_call_limit
        step_limit = self.settings.itinerary_step_limit if body.task_scope == "itinerary" else self.settings.planning_step_limit
        state = SessionState(session_id=session_id, owner_ref="capability:" + session_id,
            state_version=0, intent_snapshot=with_profile(intent, body.profile), task_scope=body.task_scope,
            budget_usage=CallBudget(limit=self.settings.planning_model_call_limit + map_limit))
        with self.factory() as db:
            db.add(PlanningSession(session_id=session_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                state_json=self._durable(state), profile_json=body.profile.model_dump(mode="json"),
                messages_json=[{"kind": "user" if body.intent else "intake", "text": body.message,
                    "message_id": str(uuid4()), "processed": body.intent is not None}] if body.message else [],
                model_limit=self.settings.planning_model_call_limit, map_limit=map_limit, step_limit=step_limit))
            db.add(PlanningEvent(session_id=session_id, state_version=0, kind="created", data={}))
            db.commit()
        return session_id, token

    def retry_with_context(self, session_id, expected_version):
        """Create one user-requested attempt with a fresh bounded budget.

        The original usage is untouched. Supplier places are copied only from
        live memory; a process restart requires a fresh search.
        """
        source = self.load(session_id)
        if source.state.state_version != expected_version:
            raise PlanningError("stale_state")
        if source.state.status != "needs_attention" or source.state.retry_generation or source.state.retry_child_ref:
            raise PlanningError("retry_not_available")
        if source.lease_id and source.lease_until > time.time():
            raise PlanningError("session_already_running")
        body = CreateSession(
            intent=source.state.intent_snapshot, profile=source.profile, task_scope=source.state.task_scope)
        child_id, token = self.create(body)
        child = self.load(child_id).state
        child.trip_ref = source.state.trip_ref or session_id
        child.retry_generation = 1
        child.selections = source.state.selections
        child.search_recipes = source.state.search_recipes
        child.intake_state = source.state.intake_state
        child.candidates = source.state.candidates
        child.candidate_ids = source.state.candidate_ids
        child.candidates_need_refresh = len(child.candidates) != len(child.candidate_ids)
        child.coverage = source.state.coverage if child.candidates else {}
        child.candidates_stale = source.state.candidates_stale
        self.save(child, expected=0, kind="retry_started", event_data={"source_session_id": session_id})
        parent = source.state
        parent.retry_child_ref = child_id
        self.save(parent, expected=expected_version, kind="retry_child_created", event_data={"child_session_id": child_id})
        return child_id, token

    def authorize(self, session_id, token):
        with self.factory() as db:
            row = db.get(PlanningSession, session_id)
            supplied = hashlib.sha256((token or "").encode()).hexdigest()
            if row is None or not secrets.compare_digest(row.token_hash, supplied):
                raise PlanningError("session_not_found", 404)

    def load(self, session_id):
        with self.factory() as db:
            row = db.get(PlanningSession, session_id)
            if row is None:
                raise PlanningError("session_not_found", 404)
            data = dict(row.state_json)
            data.update(state_version=row.state_version, status=row.status,
                budget_usage={"used": row.model_calls + row.map_calls, "limit": row.model_limit + row.map_limit})
            if row.status == "running" and row.lease_until <= time.time():
                data["status"] = "needs_attention"
                data["attention_reason"] = "server_shutdown_resume_available"
            memory = self._places.get(session_id)
            if memory and memory[0] == row.state_version:
                data["agent_message"] = memory[2]
                data["candidates"] = [p.model_dump(mode="json") for p in memory[1] if p.place_id in data["candidate_ids"]]
                data["candidates_need_refresh"] = len(data["candidates"]) != len(data["candidate_ids"])
                if len(memory) > 3:
                    data["current_plan"] = memory[3].model_dump(mode="json") if memory[3] else None
                    data["plan_needs_refresh"] = bool(data["current_plan_ref"] and not memory[3])
                    data["travel_offers"] = memory[4] if len(memory) > 4 else []
            state = SessionState.model_validate(data)
            return Snapshot(state, ProfileSnapshot.model_validate(row.profile_json), list(row.messages_json),
                {"model_calls": row.model_calls, "map_calls": row.map_calls, "steps": row.steps,
                 "model_limit": row.model_limit, "map_limit": row.map_limit, "step_limit": row.step_limit},
                row.lease_id, row.lease_until)

    def view(self, session_id, *, replayed=False):
        snapshot = self.load(session_id)
        return SessionView(state=snapshot.state, profile=snapshot.profile, usage=snapshot.usage,
            available_actions=AVAILABLE_ACTIONS, replayed=replayed)

    def replay(self, session_id, request_id, signature):
        with self.factory() as db:
            old = db.scalar(select(PlanningCommand).where(PlanningCommand.session_id == session_id,
                PlanningCommand.request_id == request_id))
            if old and old.fingerprint != signature:
                raise PlanningError("request_id_reused_with_different_payload")
            return old is not None

    def save(self, state, *, expected, kind, event_data=None, messages=None, lease_id=None,
             command=None, new_lease=None):
        """Every state-changing result wins a SQL comparison; stale work cannot overwrite edits."""
        next_state = state.model_copy(deep=True)
        next_state.state_version = expected + 1
        for question in next_state.pending_questions:
            question.state_version = next_state.state_version
        # Validate model_copy mutations at the boundary.
        try:
            next_state = SessionState.model_validate(next_state.model_dump(mode="json"))
        except ValidationError:
            raise PlanningError("invalid_state_transition", 422) from None
        with self.factory() as db:
            condition = [PlanningSession.session_id == state.session_id, PlanningSession.state_version == expected]
            if lease_id is not None:
                condition.extend([PlanningSession.lease_id == lease_id, PlanningSession.status == "running",
                    PlanningSession.lease_until > time.time()])
            values = dict(state_json=self._durable(next_state), state_version=expected + 1, status=next_state.status)
            if messages is not None:
                values["messages_json"] = messages
            if new_lease is not None:
                # Claim only an idle/expired lease, even if the user knows its version.
                condition.append(or_(PlanningSession.lease_id.is_(None), PlanningSession.lease_until <= time.time()))
                values.update(lease_id=new_lease, lease_until=time.time() + 30)
            elif next_state.status != "running":
                values.update(lease_id=None, lease_until=0)
            if db.execute(update(PlanningSession).where(*condition).values(**values)).rowcount != 1:
                raise PlanningError("stale_state_or_active_lease")
            if command:
                request_id, signature = command
                db.add(PlanningCommand(command_id=str(uuid4()), session_id=state.session_id,
                    request_id=request_id, fingerprint=signature))
            db.add(PlanningEvent(session_id=state.session_id, state_version=expected + 1,
                kind=kind, data=event_data or {}))
            db.commit()
        # Only publish supplier objects following a successful version check.
        self._places[state.session_id] = (expected + 1, next_state.candidates, next_state.agent_message, next_state.current_plan, next_state.travel_offers)
        return next_state

    def reserve(self, session_id, counter, *, lease_id=None, version=None):
        columns = {"model_calls": (PlanningSession.model_calls, PlanningSession.model_limit),
            "map_calls": (PlanningSession.map_calls, PlanningSession.map_limit),
            "steps": (PlanningSession.steps, PlanningSession.step_limit)}
        field, limit = columns[counter]
        with self.factory() as db:
            condition = [PlanningSession.session_id == session_id, field < limit]
            if lease_id:
                condition.extend([PlanningSession.lease_id == lease_id, PlanningSession.status == "running",
                    PlanningSession.lease_until > time.time()])
            if version is not None:
                condition.append(PlanningSession.state_version == version)
            if db.execute(update(PlanningSession).where(*condition).values({counter: field + 1})).rowcount != 1:
                raise PlanningError("budget_exhausted_or_obsolete")
            db.commit()

    def heartbeat(self, session_id, lease_id):
        with self.factory() as db:
            changed = db.execute(update(PlanningSession).where(PlanningSession.session_id == session_id,
                PlanningSession.lease_id == lease_id, PlanningSession.status == "running",
                PlanningSession.lease_until > time.time()).values(lease_until=time.time() + 30)).rowcount
            db.commit()
            return changed == 1

    def events(self, session_id, after=0):
        with self.factory() as db:
            rows = db.scalars(select(PlanningEvent).where(PlanningEvent.session_id == session_id,
                PlanningEvent.event_id > after).order_by(PlanningEvent.event_id).limit(100)).all()
            return [{"event_id": r.event_id, "state_version": r.state_version, "kind": r.kind, "data": r.data} for r in rows]

    def diagnostic(self, session_id, lease_id, data):
        with self.factory() as db:
            row = db.get(PlanningSession, session_id)
            if row and row.lease_id == lease_id and row.status == "running":
                db.add(PlanningEvent(session_id=session_id, state_version=row.state_version, kind="contract_repair", data=data))
                db.commit()
