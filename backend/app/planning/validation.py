"""Hard rules are independent of an LLM's quality opinion."""

from datetime import timedelta

from app.planning.clustering import same_named_place
from app.schemas.common import utc_now
from app.schemas.itinerary import DraftAudit, PlanningIssue


def validate(plan, state):
    issues = []
    def issue(code, severity, message, day=None, ids=None):
        issues.append(PlanningIssue(code=code, severity=severity, message=message, day_index=day, place_ids=ids or []))
    by_id = {p.place_id: p for p in state.candidates}
    intent = state.intent_snapshot
    if plan.retrieval_gaps:
        issue("nearby_meals_required", "blocking", "路线计算前需要围绕这些景点补齐附近餐馆候选", ids=plan.retrieval_gaps)
    expected = intent.dates.duration_days
    if expected is None and intent.dates.start_date and intent.dates.end_date:
        expected = (intent.dates.end_date - intent.dates.start_date).days + 1
    if len(plan.days) != expected:
        issue("day_coverage", "blocking", "日程天数与需求不一致")
    seen, scheduled, seen_attractions = set(), {}, []
    for day in plan.days:
        if not any(s.category == "attraction" for s in day.stops):
            issue("empty_day", "blocking", "该天没有景点安排，需要补搜或调整", day.day_index)
        if intent.dates.start_date and day.date != intent.dates.start_date + timedelta(days=day.day_index - 1):
            issue("day_date_mismatch", "blocking", "日期与相对天数不一致", day.day_index)
        if not day.hotel_place_id and len(plan.days) > 1:
            issue("hotel_missing", "blocking", "多日行程缺少住宿候选", day.day_index)
        if sum(s.category == "restaurant" for s in day.stops) < 2:
            issue("meals_missing", "blocking", "缺少景点周边2公里内的午餐或晚餐候选，请围绕当天景点补搜", day.day_index,
                  [s.place_id for s in day.stops if s.category == "attraction"])
        if day.hotel_place_id and (not day.stops or
                (day.stops[0].place_id != day.hotel_place_id and not (day.day_index == 1 and day.stops[0].category == "transport" and plan.selected_offer_ids)) or
                (day.stops[-1].place_id != day.hotel_place_id and not (day.day_index == len(plan.days) and day.stops[-1].category == "transport" and plan.selected_offer_ids))):
            issue("hotel_roundtrip_missing", "blocking", "缺少住宿出发或返回节点", day.day_index)
        if day.departure_deadline_minute is not None and day.stops:
            last = day.stops[-1]
            if last.end_minute is None:
                issue("departure_connection_unknown", "unknown", "返程接驳耗时尚未确定", day.day_index)
            elif last.end_minute > day.departure_deadline_minute:
                issue("departure_connection_missed", "blocking", "无法在返程班次出发前完成接驳及候车缓冲", day.day_index)
        pairs = [(a, b) for a, b in zip(day.stops, day.stops[1:]) if a.place_id != b.place_id]
        # Match each occurrence, not just a set of endpoints: repeated directed
        # edges at different times each need their own route observation.
        remaining = list(day.routes)
        walking = 0
        for first, second in pairs:
            leg = next((r for r in remaining if r.from_place_id == first.place_id and r.to_place_id == second.place_id), None)
            if leg is None:
                issue("route_missing", "blocking", "缺少必需路段", day.day_index, [first.place_id, second.place_id])
                continue
            remaining.remove(leg)
            if leg.status != "ok":
                issue("route_unavailable", "unknown", "该路段没有可用耗时，不得视为零分钟", day.day_index, [first.place_id, second.place_id])
            else:
                if first.end_minute is not None and second.start_minute is not None and second.start_minute < first.end_minute + leg.duration_seconds / 60:
                    issue("travel_overlap", "blocking", "安排没有包含真实交通耗时", day.day_index)
                walking += leg.distance_meters if leg.mode == "walking" else sum(s.distance_meters or 0 for s in leg.steps if s.mode == "walking")
                if (utc_now() - leg.queried_at).total_seconds() > 3600:
                    issue("route_stale", "unknown", "路线观察已过期，需要重新查询", day.day_index)
        for limit in (c for c in intent.constraints if c.kind == "walking_limit" and c.strength == "hard"):
            if walking > limit.max_daily_meters:
                issue("walking_limit", "blocking", "已知步行距离超过每日限制", day.day_index)
        for stop in day.stops:
            if stop.place_id not in by_id:
                issue("unknown_identity", "blocking", "地点没有加载可核实身份", day.day_index, [stop.place_id])
                continue
            scheduled.setdefault(stop.place_id, []).append((day, stop))
            for child in stop.child_place_ids:
                scheduled.setdefault(child, []).append((day, stop))
            if stop.category == "attraction":
                if (stop.place_id in seen or any(pid in seen for pid in stop.child_place_ids)
                        or any(same_named_place(by_id[stop.place_id], prior) for prior in seen_attractions)):
                    issue("duplicate_attraction", "blocking", "景点或内部点位重复占用行程", day.day_index, [stop.place_id])
                seen.update([stop.place_id] + stop.child_place_ids)
                seen_attractions.append(by_id[stop.place_id])
                hours = by_id[stop.place_id].facts.get("opening_windows")
                if hours and hours.value and day.date and isinstance(hours.value, list) and (not hours.valid_until or hours.valid_until >= utc_now()):
                    windows = [w for w in hours.value if isinstance(w, dict) and (w.get("date") == str(day.date) or w.get("weekday") == day.date.weekday())]
                    if stop.start_minute is not None and not any(w.get("start_minute", 0) <= stop.start_minute and w.get("end_minute", 0) >= stop.end_minute for w in windows):
                        issue("opening_conflict", "blocking", "安排不符合有证据的开放时段", day.day_index, [stop.place_id])
                else:
                    issue("opening_unknown", "unknown", "开放时段及预约条件尚未核实", day.day_index, [stop.place_id])
            if stop.start_minute is None:
                issue("schedule_unknown", "unknown", "缺少交通耗时，后续时刻暂不可确认", day.day_index, [stop.place_id])
            elif stop.category != "hotel" and stop.end_minute > 20 * 60:
                issue("day_overflow", "blocking", "活动超过草案每日20:00上限，应缩减或重排", day.day_index)
        for first, second in zip(day.stops, day.stops[1:]):
            if first.end_minute is not None and second.start_minute is not None and second.start_minute < first.end_minute:
                issue("stop_overlap", "blocking", "活动时间重叠", day.day_index)
    for selected in state.selections:
        visits = scheduled.get(selected.place_id, [])
        if selected.decision in {"include", "lock"} and not visits:
            issue("selection_missing", "blocking", "用户选择或锁定地点被遗漏", ids=[selected.place_id])
        if selected.decision == "exclude" and visits:
            issue("excluded_selected", "blocking", "排除地点仍在计划中", ids=[selected.place_id])
        if selected.date and selected.decision != "exclude" and any(d.date != selected.date for d, _ in visits):
            issue("lock_date", "blocking", "选择日期没有保留", ids=[selected.place_id])
        if selected.time_window and selected.decision != "exclude":
            start, end = selected.time_window.start, selected.time_window.end
            if not any(d.date == start.date() and s.start_minute is not None and s.start_minute <= start.hour * 60 + start.minute and s.end_minute >= end.hour * 60 + end.minute for d, s in visits):
                issue("lock_time", "blocking", "锁定时段没有保留", ids=[selected.place_id])
    for constraint in intent.constraints:
        if constraint.strength != "hard":
            continue
        visits = scheduled.get(getattr(constraint, "place_id", ""), [])
        if constraint.kind == "exclude" and visits:
            issue("excluded_constraint", "blocking", "违反地点排除约束")
        if constraint.kind in {"must_visit", "reservation", "booked_hotel"} and not visits:
            issue("constraint_missing", "blocking", "必去、预约或已订酒店未被纳入")
        if constraint.kind == "reservation":
            start, end = constraint.time_window.start, constraint.time_window.end
            if not any(d.date == start.date() and s.start_minute is not None and s.start_minute <= start.hour * 60 + start.minute and s.end_minute >= end.hour * 60 + end.minute for d, s in visits):
                issue("reservation_time", "blocking", "未满足预约日期或时段")
        if constraint.kind == "booked_hotel":
            for day in plan.days:
                if day.date and constraint.check_in <= day.date < constraint.check_out and day.hotel_place_id != constraint.place_id:
                    issue("booked_hotel_mismatch", "blocking", "已订住宿日期没有正确覆盖", day.day_index)
        if constraint.kind == "special_requirement":
            issue("special_requirement_unknown", "unknown", "特殊要求需要单独证据核实")
    for missing in plan.missing_requirements:
        issue("evidence_missing", "unknown", missing)
    status = "failed" if any(i.severity == "blocking" for i in issues) else ("unknown" if any(i.severity == "unknown" for i in issues) else "passed")
    return DraftAudit(plan_version=plan.version, hard_status=status, issues=issues)
