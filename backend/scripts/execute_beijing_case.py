"""Run the user's real Shanghai-to-Beijing request through the hosted P1 API.

Supplier objects are printed for current inspection only, never written to the report.
The optional remembered session capability stays in the ignored backend/.env.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values, set_key

BACKEND = Path(__file__).resolve().parents[1]
REQUEST = "从上海到北京玩5天，希望舒适，2人不喜欢吃辣，希望饮食清淡健康，住宿在景点附近，交通便利，更喜欢去偏人文的景点玩，喜欢古代建筑。"


def request_body():
    source = "beijing-user-request"
    evidence = {"source": "message", "source_ref": source, "confirmation": "explicit"}
    return {"intent": {"intent_id": "beijing-case-" + str(uuid4()),
        "origin": {"label": "上海", "country_code": "CN", "timezone": "Asia/Shanghai"},
        "destination": {"label": "北京", "country_code": "CN", "timezone": "Asia/Shanghai"},
        "dates": {"duration_days": 5, "timezone": "Asia/Shanghai"},
        "party": {"count": 2, "count_status": "exact", "raw": "2人"},
        "budget": {"unknown_reason": "user_did_not_provide_budget"},
        "preferences": {"interests": ["人文", "古代建筑"], "comfort_tags": ["舒适", "交通便利"]},
        "constraints": [{"kind": "special_requirement", "constraint_id": "diet-no-spicy", "strength": "soft",
            "description": "不喜欢吃辣，优先不辣餐食", "evidence": evidence},
            {"kind": "special_requirement", "constraint_id": "diet-healthy", "strength": "soft",
                "description": "饮食清淡健康", "evidence": evidence},
            {"kind": "special_requirement", "constraint_id": "stay-nearby", "strength": "soft",
                "description": "住宿在景点附近，交通便利", "evidence": evidence}],
        "field_evidence": {field: evidence for field in ["origin", "destination", "dates.duration_days", "party",
            "preferences.interests", "preferences.comfort_tags", "constraints"]},
        "notes": "出发日期、预算、城际及市内交通方式未提供，保持未知。"}, "message": REQUEST}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--remember", action="store_true")
    parser.add_argument("--resume-last", action="store_true", help="Resume this same remembered case after the user's clarification")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: hosted P1 may call real model/map APIs")
    with httpx.Client(timeout=15) as client:
        if args.resume_last:
            values = dotenv_values(BACKEND / ".env")
            session_id, token = values["P1_LAST_SESSION_ID"], values["P1_LAST_SESSION_TOKEN"]
        else:
            created = client.post(args.base_url.rstrip("/") + "/api/sessions", json=request_body())
            created.raise_for_status()
            data = created.json()
            session_id, token = data["state"]["session_id"], data["access_token"]
        header = {"Authorization": "Bearer " + token}
        url = args.base_url.rstrip("/") + "/api/sessions/" + session_id
        if args.remember:
            set_key(str(BACKEND / ".env"), "P1_LAST_SESSION_ID", session_id)
            set_key(str(BACKEND / ".env"), "P1_LAST_SESSION_TOKEN", token)
        before = client.get(url, headers=header)
        before.raise_for_status()
        data = before.json()
        baseline_usage = data["usage"]
        if args.resume_last:
            if data["state"]["intent_snapshot"]["destination"]["label"] != "北京":
                raise RuntimeError("Remembered capability does not refer to the Beijing case")
            edited = client.patch(url, headers=header, json={"request_id": str(uuid4()),
                "base_state_version": data["state"]["state_version"], "intent_patch": {
                    "notes": "用户已确认出发日期和预算暂不确定，本轮先测试候选检索。原始旅行偏好保留；城际及市内交通方式未指定。"}})
            edited.raise_for_status()
            data = edited.json()
        run = client.post(url + "/run", headers=header, json={"request_id": str(uuid4()), "base_state_version": data["state"]["state_version"]})
        run.raise_for_status()
        print(json.dumps({"phase": "running", "session_id": session_id, "request": REQUEST}, ensure_ascii=False), flush=True)
        deadline = time.monotonic() + 210
        last_progress = time.monotonic()
        while time.monotonic() < deadline:
            response = client.get(url, headers=header)
            response.raise_for_status()
            result = response.json()
            if result["state"]["status"] != "running":
                break
            if time.monotonic() - last_progress >= 20:
                print(json.dumps({"phase": "running", "usage": result["usage"]}, ensure_ascii=False), flush=True)
                last_progress = time.monotonic()
            time.sleep(0.5)
        else:
            raise RuntimeError("Hosted runtime did not stop within the observation deadline")
        events = client.get(url + "/events", headers=header)
        events.raise_for_status()
        state = result["state"]
        places = state["candidates"]
        # These supplier names and the model summary are display-only observations.
        display = {"display_candidate_names": [p["name"] for p in places], "agent_message": state.get("agent_message"),
            "pending_questions": state["pending_questions"]}
        print(json.dumps(display, ensure_ascii=False, indent=2), flush=True)
        report = {"executed_at": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
            "run_stage": "resume_same_session" if args.resume_last else "initial",
            "request": REQUEST, "final_status": state["status"], "attention_reason": state["attention_reason"],
            "state_version": state["state_version"], "intent_snapshot": state["intent_snapshot"], "usage": result["usage"],
            "run_usage_delta": {key: result["usage"][key] - baseline_usage[key] for key in ["model_calls", "map_calls", "steps"]},
            "action_sequence": [e["data"]["action_kind"] for e in events.json() if e["kind"] == "action_applied"],
            "contract_diagnostics": [e["data"] for e in events.json() if e["kind"] == "contract_repair"],
            "candidate_count": len(places), "coordinate_count": sum(p["coordinates"] is not None for p in places),
            "coordinate_systems": sorted({p["coordinates"]["crs"] for p in places if p["coordinates"]}),
            "provider_ref_count": sum(bool(p["provider_refs"]) for p in places),
            "categories": {c: sum(p["category"] == c for p in places) for c in sorted({p["category"] for p in places})},
            "pending_question_gap_ids": [q["gap_ids"] for q in state["pending_questions"]],
            "full_itinerary_generated": state["current_plan_ref"] is not None,
            "scope": "Actual user request, no scripted choice of agent action and no fabricated supplier data. P2 itinerary and P3 UI capabilities are unavailable in P1."}
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
