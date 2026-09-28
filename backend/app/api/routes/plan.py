import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_PY = ROOT / "Orchestrator" / "orchestrator.py"
SEARCH_PY = ROOT / "SearchAgent" / "search.py"


def _python_for(component: str) -> Path:
    """Use the component venv when present, otherwise reuse the API's interpreter."""
    venv = ROOT / component / ".venv"
    candidates = (venv / "bin" / "python", venv / "Scripts" / "python.exe")
    return next((path for path in candidates if path.is_file()), Path(sys.executable))


ORCHESTRATOR_PYTHON = _python_for("Orchestrator")
SEARCH_PYTHON = _python_for("SearchAgent")

router = APIRouter(prefix="/plan", tags=["plan"])


class PlanRequest(BaseModel):
    query: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    profile: dict | None = None
    basic: dict | None = None
    answers: list | None = None


def _parse_query(query: str) -> dict:
    try:
        proc = subprocess.run(
            [str(SEARCH_PYTHON), str(SEARCH_PY), "--parse"],
            input=query,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return {}


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

    try:
        proc = subprocess.run(
            [str(ORCHESTRATOR_PYTHON), str(ORCHESTRATOR_PY)],
            input=json.dumps(data, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=600,
        )
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stdout or proc.stderr).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
