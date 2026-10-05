import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[4]
QUESTION_PY = ROOT / "QuestionAgent" / "agent.py"
QUESTION_PYTHON = ROOT / "PlanAgent" / ".venv" / "bin" / "python"

router = APIRouter(prefix="/question", tags=["question"])


def _subprocess_env() -> dict:
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    return env


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
            env=_subprocess_env(),
            timeout=120,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
