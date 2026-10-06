import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_env import component_python, component_script, subprocess_env  # noqa: E402

QUESTION_PY = component_script("QuestionAgent", "agent.py")
# QuestionAgent 没有独立 venv，回退到 PlanAgent 的解释器（其依赖可覆盖）
QUESTION_PYTHON = component_python("QuestionAgent", fallback=component_python("PlanAgent"))

router = APIRouter(prefix="/question", tags=["question"])


def _subprocess_env() -> dict:
    """子进程环境：UTF-8 标准流 + 清理 macOS venv 遗留变量。"""
    return subprocess_env()


class QuestionRequest(BaseModel):
    messages: list[dict]
    has_plan: bool = False


@router.post("")
def ask_question(payload: QuestionRequest) -> dict:
    try:
        proc = subprocess.run(
            [str(QUESTION_PYTHON), str(QUESTION_PY)],
            input=json.dumps(
                {"messages": payload.messages, "has_plan": payload.has_plan},
                ensure_ascii=False,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_subprocess_env(),
            timeout=120,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
