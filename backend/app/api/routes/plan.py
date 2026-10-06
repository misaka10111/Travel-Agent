import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_PY = ROOT / "Orchestrator" / "orchestrator.py"
ORCHESTRATOR_PYTHON = ROOT / "Orchestrator" / ".venv" / "bin" / "python"
SEARCH_PY = ROOT / "SearchAgent" / "search.py"
SEARCH_PYTHON = ROOT / "SearchAgent" / ".venv" / "bin" / "python"
PLAN_PY = ROOT / "PlanAgent" / "plan.py"
PLAN_PYTHON = ROOT / "PlanAgent" / ".venv" / "bin" / "python"

router = APIRouter(prefix="/plan", tags=["plan"])


def _subprocess_env() -> dict:
    """清除 __PYVENV_LAUNCHER__，避免 macOS venv 启动器变量污染子进程的 venv 解析。"""
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    return env


class PlanRequest(BaseModel):
    user_id: str | None = None
    query: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    profile: dict | None = None
    basic: dict | None = None
    answers: list | None = None
    plan: dict | None = None
    modify: dict | None = None


def _parse_query(query: str) -> dict:
    try:
        proc = subprocess.run(
            [str(SEARCH_PYTHON), str(SEARCH_PY), "--parse"],
            input=query,
            capture_output=True,
            text=True,
            env=_subprocess_env(),
            timeout=120,
        )
        return json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return {}


def _classify_modify(instruction: str, blocks: list) -> dict:
    try:
        proc = subprocess.run(
            [str(PLAN_PYTHON), str(PLAN_PY)],
            input=json.dumps(
                {"classify": True, "instruction": instruction, "blocks": blocks},
                ensure_ascii=False,
            ),
            capture_output=True,
            text=True,
            env=_subprocess_env(),
            timeout=120,
        )
        return json.loads(proc.stdout)
    except Exception:
        return {"mode": "block", "targets": []}


def _blocks_to_plan(
    blocks: list,
    destination: str,
    start_date: str,
    end_date: str,
) -> dict:
    """把扁平 blocks 重建为 PlanAgent 的 plans 结构，供全局修改使用。"""
    by_day: dict[int, list[dict]] = {}
    for block in blocks:
        day = block.get("day") or 1
        by_day.setdefault(day, []).append(
            {
                "time": block.get("time") or "",
                "type": block.get("type") or "",
                "name": block.get("name") or "",
                "note": block.get("note") or "",
                "link": block.get("link") or "",
            }
        )
    itinerary = []
    for day in sorted(by_day):
        itinerary.append(
            {
                "day": day,
                "date": "",
                "theme": "",
                "hotel": "",
                "schedule": by_day[day],
            }
        )
    return {
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
        "plans": [{"style": "推荐方案", "summary": "", "itinerary": itinerary}],
    }


def _local_modify(
    destination: str,
    start_date: str,
    end_date: str,
    blocks: list,
    instruction: str,
    profile: dict | None,
    basic: dict | None,
) -> dict:
    """局部修改：只改选中的 block，search 命中缓存，PlanAgent 只输出修改后的 blocks。"""
    search_result: dict = {}
    if destination and start_date:
        search_payload = {
            "destination": destination,
            "start_date": start_date,
            "end_date": end_date,
        }
        if basic and basic.get("origin"):
            search_payload["origin"] = basic["origin"]
        try:
            proc = subprocess.run(
                [str(SEARCH_PYTHON), str(SEARCH_PY)],
                input=json.dumps(search_payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                env=_subprocess_env(),
                timeout=120,
            )
            search_result = json.loads(proc.stdout)
        except Exception:
            search_result = {}

    plan_payload = {
        "blocks": blocks,
        "instruction": instruction,
        "search": search_result,
        "profile": profile,
        "basic": basic,
    }
    try:
        proc = subprocess.run(
            [str(PLAN_PYTHON), str(PLAN_PY)],
            input=json.dumps(plan_payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            env=_subprocess_env(),
            timeout=120,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _merge_modified_blocks(result: dict, full_blocks: list) -> dict:
    """把局部修改结果按 id 合并回完整 blocks。"""
    if isinstance(result, dict) and isinstance(result.get("blocks"), list):
        by_id = {
            block.get("id"): block
            for block in result["blocks"]
            if block.get("id")
        }
        merged = [by_id.get(block.get("id"), block) for block in full_blocks]
        return {"blocks": merged}
    return result


@router.post("/stream")
def plan_stream(payload: PlanRequest):
    """SSE 流式规划：逐节点返回 search/plan/validate 进度。"""
    destination = payload.destination
    start_date = payload.start_date
    end_date = payload.end_date
    parsed: dict = {}

    if payload.query and not destination:
        parsed = _parse_query(payload.query)
        destination = parsed.get("destination")
        start_date = parsed.get("start_date")
        end_date = parsed.get("end_date")

    if not destination or not start_date:
        return {"error": "无法从输入中解析出目的地或日期"}

    basic = dict(payload.basic or {})
    if parsed:
        if parsed.get("origin") and not basic.get("origin"):
            basic["origin"] = parsed["origin"]
        if parsed.get("travelers") and not basic.get("travelers"):
            basic["travelers"] = parsed["travelers"]
        if parsed.get("budget") and not basic.get("total_budget"):
            basic["total_budget"] = str(parsed["budget"])
        if parsed.get("purposes") and not basic.get("purposes"):
            basic["purposes"] = parsed["purposes"]

    data: dict = {
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
    }
    if payload.user_id:
        data["user_id"] = payload.user_id
    if payload.profile:
        data["profile"] = payload.profile
    if basic:
        data["basic"] = basic
    if payload.answers:
        data["answers"] = payload.answers

    def event_stream():
        missing: list[str] = []
        if not basic.get("origin") and not parsed.get("origin"):
            missing.append("origin")
        if missing:
            clarify = {
                "type": "clarify",
                "missing": missing,
                "destination": destination,
                "start_date": start_date,
                "end_date": end_date,
            }
            yield f"data: {json.dumps(clarify, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        proc = subprocess.Popen(
            [str(ORCHESTRATOR_PYTHON), str(ORCHESTRATOR_PY), "--stream"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_subprocess_env(),
        )
        proc.stdin.write(json.dumps(data, ensure_ascii=False))
        proc.stdin.close()
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    yield f"data: {line}\n\n"
            proc.wait()
            if proc.returncode != 0:
                err = (proc.stderr.read() or "").strip()
                if err:
                    yield f"data: {json.dumps({'type': 'error', 'error': err}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # 客户端断开（关闭页面/切换路由/abort）时终止孤儿编排进程，避免继续消耗 API
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _modify_plan(
    destination: str,
    start_date: str,
    end_date: str,
    plan: dict,
    modify: dict,
    profile: dict | None,
    basic: dict | None,
) -> dict:
    """完整计划修改：全局修改（global）或 block 修改（block），基于上一版 plan。"""
    search_result: dict = {}
    if destination and start_date:
        search_payload = {
            "destination": destination,
            "start_date": start_date,
            "end_date": end_date,
        }
        if basic and basic.get("origin"):
            search_payload["origin"] = basic["origin"]
        try:
            proc = subprocess.run(
                [str(SEARCH_PYTHON), str(SEARCH_PY)],
                input=json.dumps(search_payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                env=_subprocess_env(),
                timeout=120,
            )
            search_result = json.loads(proc.stdout)
        except Exception:
            search_result = {}

    plan_payload = {
        "plan": plan,
        "modify": modify,
        "search": search_result,
        "profile": profile,
        "basic": basic,
    }
    try:
        proc = subprocess.run(
            [str(PLAN_PYTHON), str(PLAN_PY)],
            input=json.dumps(plan_payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            env=_subprocess_env(),
            timeout=300,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@router.post("")
def create_plan(payload: PlanRequest) -> dict:
    destination = payload.destination
    start_date = payload.start_date
    end_date = payload.end_date
    parsed: dict = {}

    if payload.query and not destination:
        parsed = _parse_query(payload.query)
        destination = parsed.get("destination")
        start_date = parsed.get("start_date")
        end_date = parsed.get("end_date")

    if not destination or not start_date:
        return {"error": "无法从输入中解析出目的地或日期"}

    data: dict = {
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
    }
    if payload.user_id:
        data["user_id"] = payload.user_id
    # 把 query 里解析出的出发地/人数/预算/目的合并进 basic（优先保留已填的 tripInfo）
    basic = dict(payload.basic or {})
    if parsed:
        if parsed.get("origin") and not basic.get("origin"):
            basic["origin"] = parsed["origin"]
        if parsed.get("travelers") and not basic.get("travelers"):
            basic["travelers"] = parsed["travelers"]
        if parsed.get("budget") and not basic.get("total_budget"):
            basic["total_budget"] = str(parsed["budget"])
        if parsed.get("purposes") and not basic.get("purposes"):
            basic["purposes"] = parsed["purposes"]
    if payload.profile:
        data["profile"] = payload.profile
    if basic:
        data["basic"] = basic
    if payload.answers:
        data["answers"] = payload.answers

    # 已由 QuestionAgent 判断出 mode/targets 时，直接执行，不再二次分类
    if (
        payload.modify
        and payload.modify.get("instruction")
        and payload.modify.get("blocks")
        and payload.modify.get("mode")
    ):
        blocks = payload.modify.get("blocks") or []
        instruction = payload.modify.get("instruction") or ""
        if payload.modify.get("mode") == "global":
            plan = _blocks_to_plan(blocks, destination, start_date, end_date)
            return _modify_plan(
                destination,
                start_date,
                end_date,
                plan,
                {"mode": "global", "instruction": instruction},
                payload.profile,
                basic,
            )
        targets = payload.modify.get("targets") or []
        if targets:
            target_names = {
                t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else "")
                for t in targets
            }
            target_names = {n for n in target_names if n}
            if not any(k in instruction for k in ("换", "替换", "换成")) and any(
                k in instruction for k in ("删", "去掉", "不要", "取消", "移除")
            ):
                all_blocks = payload.modify.get("blocks") or []
                return {
                    "blocks": [
                        b for b in all_blocks
                        if (b.get("name") or "") not in target_names
                    ]
                }
            blocks = [
                b
                for b in blocks
                if (b.get("name") or "") in target_names
            ] or blocks
        result = _local_modify(
            destination,
            start_date,
            end_date,
            blocks,
            instruction,
            payload.profile,
            basic,
        )
        return _merge_modified_blocks(result, payload.modify.get("blocks") or [])

    # 自然语言修改：没有 mode 时，先让 PlanAgent 判断 global / block，再执行对应修改
    if payload.modify and payload.modify.get("instruction") and payload.modify.get("blocks"):
        blocks = payload.modify.get("blocks") or []
        instruction = payload.modify.get("instruction") or ""
        classification = _classify_modify(instruction, blocks)
        if classification.get("mode") == "global":
            plan = _blocks_to_plan(blocks, destination, start_date, end_date)
            return _modify_plan(
                destination,
                start_date,
                end_date,
                plan,
                {"mode": "global", "instruction": instruction},
                payload.profile,
                basic,
            )
        targets = classification.get("targets") or []
        if targets:
            target_names = {
                t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else "")
                for t in targets
            }
            target_names = {n for n in target_names if n}
            if not any(k in instruction for k in ("换", "替换", "换成")) and any(
                k in instruction for k in ("删", "去掉", "不要", "取消", "移除")
            ):
                all_blocks = payload.modify.get("blocks") or []
                return {
                    "blocks": [
                        b for b in all_blocks
                        if (b.get("name") or "") not in target_names
                    ]
                }
            blocks = [
                b
                for b in blocks
                if (b.get("name") or "") in target_names
            ] or blocks
        result = _local_modify(
            destination,
            start_date,
            end_date,
            blocks,
            instruction,
            payload.profile,
            basic,
        )
        return _merge_modified_blocks(result, payload.modify.get("blocks") or [])

    # 完整计划修改：modify 含 mode（global/block），且带上一版 plan
    if payload.modify and payload.plan and payload.modify.get("mode"):
        return _modify_plan(
            destination,
            start_date,
            end_date,
            payload.plan,
            payload.modify,
            payload.profile,
            basic,
        )

    # 局部修改：modify 含 blocks，只改选中块，不走完整 orchestrator
    if payload.modify and payload.modify.get("blocks"):
        result = _local_modify(
            destination,
            start_date,
            end_date,
            payload.modify.get("blocks") or [],
            payload.modify.get("instruction") or "",
            payload.profile,
            basic,
        )
        return _merge_modified_blocks(result, payload.modify.get("blocks") or [])

    if payload.modify:
        data["modify"] = payload.modify

    try:
        proc = subprocess.run(
            [str(ORCHESTRATOR_PYTHON), str(ORCHESTRATOR_PY)],
            input=json.dumps(data, ensure_ascii=False),
            capture_output=True,
            text=True,
            env=_subprocess_env(),
            timeout=600,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
