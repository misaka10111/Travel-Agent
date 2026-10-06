"""Keep itinerary blocks stable when updating an existing plan."""

from copy import deepcopy
from datetime import date, timedelta


LOCATION_FIELDS = ("lng", "lat", "poi_id", "link", "map_url", "options", "selected_option", "rating", "distance_m", "distance_km")


def merge_block_edits(originals: list[dict], edits: list[dict]) -> list[dict]:
    """Only accept edits for requested IDs; a new place must not inherit old coordinates."""
    by_id = {str(item.get("id")): item for item in edits if isinstance(item, dict) and item.get("id")}
    merged = []
    for original in originals:
        edit = by_id.get(str(original.get("id")))
        if not edit:
            merged.append(deepcopy(original))
            continue
        block = {**deepcopy(original), **edit, "id": original.get("id")}
        if edit.get("name") and edit["name"] != original.get("name"):
            for field in LOCATION_FIELDS:
                if field not in edit:
                    block.pop(field, None)
            if "price" not in edit:
                block.pop("price", None)
                block.pop("price_basis", None)
            for field in ("unit_price", "price_known"):
                if field not in edit:
                    block.pop(field, None)
        merged.append(block)
    return merged


def rebuild_itineraries(plan: dict) -> dict:
    """Rebuild nested schedules from blocks without discarding meal options or IDs."""
    result = deepcopy(plan)
    groups: dict[str, dict[int, list[dict]]] = {}
    summaries = {p.get("style"): p.get("summary") or "" for p in result.get("plans") or []}
    summaries.update(result.get("summaries") or {})
    day_metadata = {(str(style.get("style") or "推荐方案"), int(day.get("day") or 1)): day
                    for style in result.get("plans") or [] for day in style.get("itinerary") or []}
    for block in result.get("blocks") or []:
        style = str(block.get("plan_style") or "推荐方案")
        day = int(block.get("day") or 1)
        groups.setdefault(style, {}).setdefault(day, []).append(deepcopy(block))
    plans = []
    for style, days in groups.items():
        itinerary = []
        for day, schedule in sorted(days.items()):
            schedule.sort(key=lambda item: item.get("time") or "99:99")
            date_value = next((item.get("date") for item in schedule if item.get("date")), "")
            if not date_value and result.get("start_date"):
                try:
                    date_value = (date.fromisoformat(result["start_date"]) + timedelta(days=day - 1)).isoformat()
                except ValueError:
                    pass
            for item in schedule:
                item["plan_style"] = style
                item["day"] = day
                item["date"] = date_value
            metadata = day_metadata.get((style, day), {})
            metadata = {key: value for key, value in metadata.items() if key not in ("hotel", "hotel_link", "meals")}
            hotel = next((item for item in schedule if item.get("type") == "酒店"), None)
            if hotel:
                metadata.update(hotel=hotel.get("name") or "", hotel_link=hotel.get("link") or "")
            window = next((item.get("activity_window") for item in schedule if isinstance(item.get("activity_window"), dict)), metadata.get("activity_window"))
            itinerary.append({**metadata, "day": day, "date": date_value, "theme": metadata.get("theme") or "", "schedule": schedule,
                              **({"activity_window": deepcopy(window)} if isinstance(window, dict) else {})})
        plans.append({"style": style, "summary": summaries.get(style) or "按你的旅行需求安排", "itinerary": itinerary})
    result["plans"] = plans
    duration = 0
    try:
        duration = (date.fromisoformat(result["end_date"]) - date.fromisoformat(result["start_date"])).days + 1
    except (KeyError, ValueError, TypeError):
        pass
    result["days"] = max(duration, max((int(block.get("day") or 1) for block in result.get("blocks") or []), default=0))
    return result
