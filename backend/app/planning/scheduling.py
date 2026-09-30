from datetime import datetime, time, timedelta
from math import ceil
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.schemas.common import utc_now
from app.schemas.itinerary import DraftDay, Stop


def schedule(index, date, attractions, hotel, restaurants, routes, intent, children=None):
    zone = ZoneInfo(intent.dates.timezone or intent.destination.timezone or "Asia/Shanghai")
    reference = date or utc_now().astimezone(zone).date()
    current = 9 * 60
    chain = []
    if hotel:
        chain.append((hotel, 1, None))
    for i, place in enumerate(attractions):
        chain.append((place, 120, None))
        if i == 0 and restaurants:
            chain.append((restaurants[0], 60, 12 * 60))
    if len(restaurants) > 1:
        chain.append((restaurants[1], 60, 18 * 60))
    if hotel:
        chain.append((hotel, 1, None))
    day = DraftDay(day_index=index, date=date, hotel_place_id=hotel.place_id if hotel else None)
    known = True
    for i, (place, dwell, earliest) in enumerate(chain):
        if i and chain[i - 1][0].place_id != place.place_id:
            depart = datetime.combine(reference, time(0), zone) + timedelta(minutes=current)
            leg = routes.leg(chain[i - 1][0], place, depart)
            day.routes.append(leg)
            if leg.status == "ok" and known:
                current += ceil(leg.duration_seconds / 60) + 10
            else:
                known = False
        if earliest:
            current = max(current, earliest)
        start, end = (current, current + dwell) if known and current + dwell <= 1440 else (None, None)
        locked = any(s.place_id == place.place_id and s.decision == "lock" for s in getattr(intent, "_selections", []))
        day.stops.append(Stop(stop_id=str(uuid4()), place_id=place.place_id, category=place.category,
            start_minute=start, end_minute=end, dwell_minutes=dwell, locked=locked,
            child_place_ids=(children or {}).get(place.place_id, []),
            reason="停留时长为可调整的草案假设" if place.category == "attraction" else "食宿候选，具体服务信息待核实"))
        current += dwell + (15 if place.category == "attraction" else 0)
    if date is None:
        day.notes.append("日期待定；路线查询日期仅为参考，时刻表不是旅行日确认结果")
    day.notes.append("09:00 开始、景点停留120分钟、景点间休息15分钟、交通缓冲10分钟均为草案假设")
    return day
