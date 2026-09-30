from app.runtime.guards import PlanningError


def edit(previous, changes, state):
    draft = previous.model_copy(deep=True)
    by_id = {p.place_id: p for p in state.candidates}
    locked = {s.place_id for s in state.selections if s.decision == "lock"}
    for change in changes:
        if change.kind in {"remove_visit", "move_visit"}:
            found = next(((d, s) for d in draft.days for s in d.stops if s.stop_id == change.visit_id), None)
            if not found:
                raise PlanningError("unknown_visit", 422)
            day, stop = found
            if stop.category != "attraction":
                raise PlanningError("only_attraction_visits_are_editable", 422)
            if stop.locked or stop.place_id in locked or set(stop.child_place_ids) & locked:
                raise PlanningError("locked_visit_edit_conflict", 422)
            day.stops.remove(stop)
            if change.kind == "move_visit":
                target = next((d for d in draft.days if (change.day_index and d.day_index == change.day_index) or (change.date and d.date == change.date)), None)
                if not target:
                    raise PlanningError("move_outside_trip", 422)
                target.stops.append(stop)
        elif change.kind == "add_visit":
            from app.schemas.itinerary import Stop
            from uuid import uuid4
            place = by_id.get(change.place_id)
            target = next((d for d in draft.days if d.date == change.date), None)
            if not place or place.category != "attraction" or not target:
                raise PlanningError("invalid_add_visit", 422)
            target.stops.append(Stop(stop_id=str(uuid4()), place_id=place.place_id, category="attraction", dwell_minutes=120))
        elif change.kind == "replace_hotel":
            if {d.hotel_place_id for d in draft.days} & locked:
                raise PlanningError("locked_hotel_edit_conflict", 422)
            place = by_id.get(change.hotel_place_id)
            if not place or place.category != "hotel":
                raise PlanningError("hotel_requires_refresh", 422)
            for day in draft.days:
                day.hotel_place_id = place.place_id
    return draft
