"""Explicit finite live smoke: real Plan/Q model and Amap, isolated durable DB.

Run from repo: .venv/Scripts/python.exe backend/scripts/smoke_p1.py --live
No token, key, model response or supplier payload is written to the report.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.client import JsonModelClient
from app.agents.plan_agent import PlanAgent
from app.agents.question_agent import QuestionAgent
from app.config import Settings
from app.migrations.planning import migrate_planning
from app.runtime.executor import PlanningRuntime
from app.runtime.store import SessionStore
from app.schemas.planning_api import CreateSession
from app.tools.registry import ToolRegistry


async def smoke():
    settings = Settings(_env_file=BACKEND / ".env", planning_model_call_limit=8,
        planning_map_call_limit=4, planning_step_limit=6, planning_run_timeout_seconds=150)
    client = JsonModelClient(settings)
    with tempfile.TemporaryDirectory(prefix="travel-p1-smoke-") as directory:
        engine = create_engine("sqlite:///" + str(Path(directory) / "smoke.sqlite"), connect_args={"check_same_thread": False})
        migrate_planning(engine)
        factory = sessionmaker(bind=engine)
        store = SessionStore(factory, settings)
        runtime = PlanningRuntime(store, PlanAgent(client), ToolRegistry(QuestionAgent(client), settings), settings)
        body = CreateSession.model_validate({"intent": {"intent_id": "smoke-shanghai", "destination": {
            "label": "上海", "country_code": "CN", "timezone": "Asia/Shanghai"}, "dates": {
                "start_date": "2026-10-01", "end_date": "2026-10-05", "duration_days": 5, "timezone": "Asia/Shanghai"},
            "preferences": {"interests": ["传统园林", "历史建筑"], "travel_modes": ["walking", "transit"], "pace": "relaxed"}},
            "profile": {"travel_style": ["避免太累"]},
            "message": "上海玩五天，喜欢传统园林和历史建筑，希望去豫园、外滩，不希望重复景点或来回折返。人数暂时没填，请先只问人数。人数明确后，本轮只搜索一次景点候选，关键词只用豫园和外滩；返回候选后暂停，路线规划在 P2。"})
        sid, _ = store.create(body)

        async def run_once():
            state = store.load(sid).state
            state.status, state.attention_reason = "running", None
            lease = str(uuid4())
            store.save(state, expected=state.state_version, kind="smoke_run", new_lease=lease)
            await runtime.run(sid, lease)

        try:
            await run_once()
            first = store.load(sid)
            question_count = len(first.state.pending_questions)
            first_status = first.state.status
            first_gaps = [q.gap_ids for q in first.state.pending_questions]
            if first.state.status == "waiting_user":
                question = first.state.pending_questions[0]
                if question.gap_ids != ["party"]:
                    raise RuntimeError("Smoke expected only a party question; other facts must not be invented.")
                state = first.state
                messages = first.messages + [{"kind": "answer", "question": question.model_dump(mode="json"),
                    "answer": {"request_id": "smoke-answer", "question_id": question.question_id,
                        "state_version": state.state_version, "text": "两位成人，一共2人", "option_ids": []}, "processed": False}]
                state.pending_questions, state.status = [], "draft"
                store.save(state, expected=state.state_version, kind="smoke_answer", messages=messages)
                await run_once()
            last = store.load(sid)
            candidates = last.state.candidates
            restored = SessionStore(factory, settings).load(sid)
            events = store.events(sid)
            result = {"executed_at": datetime.now(timezone.utc).isoformat(), "city": "上海", "duration_days": 5,
                "services": "real DeepSeek-compatible JSON model and real Amap Web service",
                "first_status": first_status, "question_count": question_count, "question_gap_ids": first_gaps,
                "final_status": last.state.status, "attention_reason": last.state.attention_reason,
                "agent_message_persisted": restored.state.agent_message is not None,
                "party_count": last.state.intent_snapshot.party.count, "candidate_count": len(candidates),
                "coordinate_count": sum(p.coordinates is not None for p in candidates),
                "coordinate_systems": sorted({p.coordinates.crs for p in candidates if p.coordinates}),
                "provider_ref_count": sum(bool(p.provider_refs) for p in candidates),
                "usage": last.usage, "action_sequence": [e["data"]["action_kind"] for e in events if e["kind"] == "action_applied"],
                "contract_diagnostics": [e["data"] for e in events if e["kind"] == "contract_repair"],
                "restart_needs_refresh": restored.state.candidates_need_refresh,
                "itinerary_generated": last.state.current_plan_ref is not None,
                "passed": first_status == "waiting_user" and last.state.status == "needs_attention"
                    and last.state.attention_reason != "budget_exhausted_or_obsolete"
                    and any(e["data"].get("action_kind") == "pause" for e in events)
                    and last.state.intent_snapshot.party.count == 2 and len(candidates) > 0 and restored.state.candidates_need_refresh,
                "scenario_scope": "Explicit bounded request: one party question, one two-keyword candidate search, then Plan chooses pause; not an open-ended itinerary benchmark",
                "limits": "P1 candidate preparation only; itinerary route quality belongs to P2; UI map belongs to P3"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result
        finally:
            await runtime.shutdown()
            engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Authorize the bounded real API smoke")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: this smoke calls real model/map APIs")
    result = asyncio.run(smoke())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
