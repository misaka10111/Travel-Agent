"""Orchestrator：用 LangGraph 编排 SearchAgent → PlanAgent ⇄ ValidateAgent。

图结构：
  START → search → plan → validate
            validate --通过/达到最大轮数--> END
            validate --不通过--> plan（携带 feedback 重新生成）

用法：
  echo '{"destination":"宁波","start_date":"2026-10-01","end_date":"2026-10-03","profile":{...},"basic":{...},"answers":[...]}' \
    | python orchestrator.py

查看图：
  python orchestrator.py --graph
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

ROOT = Path(__file__).resolve().parent.parent

SEARCH_PY = ROOT / "SearchAgent" / "search.py"
SEARCH_PYTHON = ROOT / "SearchAgent" / ".venv" / "bin" / "python"
PLAN_PY = ROOT / "PlanAgent" / "plan.py"
PLAN_PYTHON = ROOT / "PlanAgent" / ".venv" / "bin" / "python"
VALIDATE_PY = ROOT / "ValidateAgent" / "validate.py"
VALIDATE_PYTHON = ROOT / "ValidateAgent" / ".venv" / "bin" / "python"

MAX_ITERATIONS = 1

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/api")


def _fetch_profile(user_id: str) -> dict:
    """从后端拉取用户画像；失败时返回空画像。"""
    url = f"{BACKEND_URL}/profile/{urllib.parse.quote(user_id)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
        profile.pop("user_id", None)
        profile.pop("created_at", None)
        profile.pop("updated_at", None)
        return profile
    except Exception:
        return {}


def _save_trip_memory(user_id: str, plan: dict, data: dict) -> dict | None:
    """把本次行程保存到后端情节记忆；失败返回 None。"""
    payload = {
        "user_id": user_id,
        "destination": plan.get("destination") or data.get("destination") or "",
        "start_date": plan.get("start_date") or data.get("start_date") or "",
        "end_date": plan.get("end_date") or data.get("end_date") or "",
        "final_plan": plan,
    }
    url = f"{BACKEND_URL}/trip-memory"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


class State(TypedDict, total=False):
    destination: str
    start_date: str
    end_date: str
    profile: dict
    basic: dict
    answers: list
    modify: dict
    search: dict
    plan: dict
    audit: dict
    feedback: str
    iteration: int
    history: list


def _call(python: Path, script: Path, payload: dict) -> dict:
    # 🌟 强行替换 Linux 路径为 Windows 路径
    python_str = str(python).replace("bin\\python", "Scripts\\python.exe").replace("bin/python", "Scripts\\python.exe")
    
    # 🌟 兜底：如果替换后的路径依然不存在，直接用当前环境的 Python！
    import os
    import sys
    if not os.path.exists(python_str):
        print(f"====== DEBUG: 路径 {python_str} 不存在，改用当前 Python: {sys.executable}", file=sys.stderr)
        python_str = sys.executable

    proc = subprocess.run(
        [python_str, str(script)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}


def search_node(state: State) -> dict:
    basic = state.get("basic") or {}
    payload = {
        "destination": state["destination"],
        "start_date": state["start_date"],
        "end_date": state.get("end_date"),
    }
    if basic.get("origin"):
        payload["origin"] = basic["origin"]
    # 把用户画像里的预算、人数、旅行目的传给搜索，用于餐饮搜索的菜系/预算过滤
    if basic:
        payload["basic"] = {
            k: basic.get(k)
            for k in ("total_budget", "travelers", "purposes")
            if basic.get(k)
        }
    result = _call(SEARCH_PYTHON, SEARCH_PY, payload)
    return {"search": result}


def plan_node(state: State) -> dict:
    payload = {
        "profile": state.get("profile"),
        "search": state.get("search"),
        "basic": state.get("basic"),
        "answers": state.get("answers"),
    }
    if state.get("feedback"):
        payload["feedback"] = state["feedback"]
    if state.get("modify"):
        payload["modify"] = state["modify"]
    result = _call(PLAN_PYTHON, PLAN_PY, payload)
    return {"plan": result, "iteration": state.get("iteration", 0) + 1}


def _plan_for_validate(plan: dict | None) -> dict | None:
    """折线只给前端画图用，送审时去掉，只保留每段路线的方式和耗时。"""
    if not isinstance(plan, dict) or not plan.get("legs"):
        return plan
    trimmed = dict(plan)
    trimmed["legs"] = [{k: v for k, v in leg.items() if k != "polyline"} for leg in plan["legs"]]
    return trimmed


def validate_node(state: State) -> dict:
    result = _call(
        VALIDATE_PYTHON,
        VALIDATE_PY,
        {
            "plan": _plan_for_validate(state.get("plan")),
            "profile": state.get("profile"),
            "search": state.get("search"),
            "basic": state.get("basic"),
            "answers": state.get("answers"),
        },
    )
    history = list(state.get("history") or [])
    history.append(
        {
            "iteration": state.get("iteration", 0),
            "passed": result.get("passed"),
            "issues": result.get("issues"),
        }
    )
    return {
        "audit": result,
        "history": history,
        "feedback": result.get("feedback"),
    }


def should_continue(state: State) -> str:
    if state.get("audit", {}).get("passed") or state.get("iteration", 0) >= MAX_ITERATIONS:
        return "end"
    return "plan"


def should_validate(state: State) -> str:
    # modify 流程跳过 validate，直接返回修改后的计划
    if state.get("modify"):
        return "end"
    return "validate"


def build_graph():
    graph = StateGraph(State)
    graph.add_node("search", search_node)
    graph.add_node("plan", plan_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "search")
    graph.add_edge("search", "plan")
    graph.add_conditional_edges("plan", should_validate, {"validate": "validate", "end": END})
    graph.add_conditional_edges("validate", should_continue, {"plan": "plan", "end": END})
    return graph.compile(checkpointer=MemorySaver())


def main() -> None:
    if "--graph" in sys.argv:
        graph = build_graph()
        print(graph.get_graph().draw_mermaid())
        return

    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    else:
        raw = " ".join(sys.argv[1:]).strip()
    if not raw:
        print(json.dumps({"error": "empty input"}, ensure_ascii=False))
        return

    try:
        data = json.loads(raw)
        if data.get("user_id") and not data.get("profile"):
            data["profile"] = _fetch_profile(data["user_id"])
        graph = build_graph()
        result = graph.invoke(data, {"configurable": {"thread_id": "orchestrator"}})
        saved_memory = None
        if data.get("user_id"):
            saved_memory = _save_trip_memory(
                data["user_id"], result.get("plan") or {}, data
            )
        output = {
            "search": result.get("search"),
            "plan": result.get("plan"),
            "passed": (result.get("audit") or {}).get("passed"),
            "history": result.get("history"),
            "saved_memory": saved_memory,
        }
    except Exception as exc:  # noqa: BLE001
        output = {"error": str(exc)}

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
