"""旅行参数校验：保留已知信息，只追问规划仍缺少的必要信息。"""

import math
import re
from datetime import date, timedelta


REQUIRED_FIELDS = (
    ("destination", "目的地"),
    ("start_date", "出发日期"),
    ("end_date", "返程日期或游玩天数"),
    ("origin", "出发地"),
    ("travelers", "出行人数"),
    ("total_budget", "本次旅行总预算（也可以填不限）"),
)
TEXT_FIELDS = ("destination", "origin", "food_keyword", "notes")
LIST_FIELDS = ("purposes", "requested_pois", "budget_tiers")


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").replace(",", "").replace("，", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([万千]?)\s*(?:元|人民币)?", text)
    if not match:
        return None
    number = float(match[1]) * {"": 1, "千": 1000, "万": 10000}[match[2]]
    return number if math.isfinite(number) and number > 0 else None


def normalize_trip_data(extracted: dict, previous: dict | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    known = dict(previous or {})
    known.update({key: value for key, value in extracted.items() if value is not None and value != ""})
    result = {}
    for key in TEXT_FIELDS:
        if isinstance(known.get(key), str) and known[key].strip():
            result[key] = known[key].strip()
    for key in LIST_FIELDS:
        value = known.get(key)
        if isinstance(value, str):
            value = re.split(r"[,，、]", value)
        if isinstance(value, list):
            values = list(dict.fromkeys(v.strip() for v in value if isinstance(v, str) and v.strip()))
            if key == "budget_tiers":
                values = [v for v in values if v in ("经济", "舒适", "豪华", "不设限")]
            if values:
                result[key] = values

    travelers = str(known.get("travelers") or "")
    counts = re.findall(r"(?<![\d.])(\d+)\s*(?:位|个)?(?:成人|大人|孩子|儿童|老人|人|大|小)", travelers)
    count = sum(int(v) for v in counts) if counts else _number(travelers)
    if count and int(count) == count:
        result["travelers"] = f"{int(count)}人"

    duration = _number(known.get("duration_days"))
    if duration and int(duration) == duration and duration <= 365:
        result["duration_days"] = int(duration)
    # 用户更新天数后，按新天数重算返程日期，避免沿用旧值。
    if extracted.get("duration_days") and not extracted.get("end_date"):
        known.pop("end_date", None)
    for key in ("start_date", "end_date"):
        try:
            parsed = date.fromisoformat(str(known.get(key) or ""))
            if parsed >= today:
                result[key] = parsed.isoformat()
        except ValueError:
            pass
    if result.get("start_date"):
        start = date.fromisoformat(result["start_date"])
        if result.get("end_date") and result["end_date"] < result["start_date"]:
            result.pop("end_date")
        if not result.get("end_date") and result.get("duration_days"):
            result["end_date"] = (start + timedelta(days=result["duration_days"] - 1)).isoformat()

    if known.get("budget_per_person") and ("budget_per_person" in extracted or "travelers" in extracted) and "total_budget" not in extracted:
        known.pop("total_budget", None)
    total = _number(known.get("total_budget"))
    per_person = _number(known.get("budget_per_person"))
    if per_person:
        result["budget_per_person"] = per_person
        if count and not total:
            total = per_person * int(count)
    if total:
        result["total_budget"] = total
        if result.get("budget_tiers") == ["不设限"]:
            result.pop("budget_tiers")
    unlimited = known.get("budget_unlimited") is True or known.get("total_budget") in ("不限", "不设限")
    if extracted.get("total_budget") and _number(extracted["total_budget"]):
        unlimited = False
    if unlimited:
        result["budget_unlimited"] = True
        result.pop("total_budget", None)
        result["budget_tiers"] = ["不设限"]
    return result


def complete_trip_request(extracted: dict, previous: dict | None = None, today: date | None = None) -> dict:
    data = normalize_trip_data(extracted, previous, today)
    missing = [
        field for field, _ in REQUIRED_FIELDS
        if not data.get(field)
        and not (field == "total_budget" and (data.get("budget_unlimited") or data.get("budget_per_person")))
        and not (field == "end_date" and not data.get("start_date") and data.get("duration_days"))
    ]
    if not missing:
        return {"action": "plan", "data": data}
    labels = [label for field, label in REQUIRED_FIELDS if field in missing]
    return {
        "action": "ask",
        "field": "trip_details",
        "missing": missing,
        "data": data,
        "question": f"还需要补充：{'、'.join(labels)}。直接用文字告诉我即可，也可以一次补充多项。",
        "options": [],
    }
