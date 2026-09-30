"""Explicit live acceptance: natural language -> Plan-led P2, bounded calls.

Only own aggregate metadata may be written; supplier content stays in memory.
The optional capability is stored only in the ignored backend .env.
"""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values, set_key

REQUEST = "从上海到北京玩5天，希望舒适，2人不喜欢吃辣，希望饮食清淡健康，住宿在景点附近，交通便利，更喜欢去偏人文的景点玩，喜欢古代建筑。出发日期和预算暂不确定，请先做第1到5天的草案，缺失信息保留未知。"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--remember", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required: this invokes paid model/map services")
    with httpx.Client(base_url=args.base_url, timeout=20) as client:
        if args.resume:
            values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
            sid, token = values["P2_LAST_SESSION_ID"], values["P2_LAST_SESSION_TOKEN"]
        else:
            response = client.post("/api/sessions", json={"message": REQUEST, "task_scope": "itinerary"})
            response.raise_for_status()
            created = response.json()
            sid, token = created["state"]["session_id"], created["access_token"]
        if args.remember:
            path = Path(__file__).resolve().parents[1] / ".env"
            set_key(path, "P2_LAST_SESSION_ID", sid)
            set_key(path, "P2_LAST_SESSION_TOKEN", token)
        headers = {"Authorization": "Bearer " + token}
        url = "/api/sessions/" + sid
        current = client.get(url, headers=headers).json()
        response = client.post(url + "/run", headers=headers, json={"request_id": str(uuid4()), "base_state_version": current["state"]["state_version"]})
        response.raise_for_status()
        deadline, last = time.monotonic() + 600, None
        while time.monotonic() < deadline:
            result = client.get(url, headers=headers).json()
            state = result["state"]
            stamp = (state["state_version"], state["status"], result["usage"]["model_calls"], result["usage"]["map_calls"])
            if stamp != last:
                print(json.dumps({"progress": stamp}, ensure_ascii=False), flush=True)
                last = stamp
            if state["status"] != "running":
                break
            time.sleep(2)
        plan = state.get("current_plan")
        metadata = {"session_id": sid, "input_path": "natural_language", "status": state["status"],
            "attention_reason": state.get("attention_reason"), "usage": result["usage"], "coverage": state["coverage"],
            "days": len(plan["days"]) if plan else 0, "plan_status": plan["status"] if plan else None,
            "audit": {"hard_status": plan["audit"]["hard_status"], "experience_status": plan["audit"]["experience_status"],
                "issue_codes": sorted({i["code"] for i in plan["audit"]["issues"]})} if plan and plan["audit"] else None}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
        if plan:
            names = {p["place_id"]: p["name"] for p in state["candidates"]}
            for day in plan["days"]:
                print(json.dumps({"day": day["day_index"], "date": day["date"],
                    "stops": [names.get(s["place_id"], "needs_refresh") for s in day["stops"]],
                    "routes": [{"mode": r["mode"], "status": r["status"], "minutes": r["duration_seconds"] / 60 if r["duration_seconds"] is not None else None} for r in day["routes"]]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
