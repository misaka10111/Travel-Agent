from datetime import timedelta
from uuid import uuid4

from app.planning.clustering import allocate, identity_groups, meters, order_day_visits, same_visit_area
from app.planning.budgeting import summarize_costs
from app.planning.ranking import hotel_ranking, nearby_ranking, preference_score
from app.planning.routing import RoutePlanner
from app.planning.scheduling import schedule
from app.planning.transfers import choose_transfers
from app.runtime.guards import PlanningError
from app.schemas.itinerary import ItineraryDraft


def compute(state, provider, route_limit, previous=None):
    intent = state.intent_snapshot
    count = intent.dates.duration_days
    if count is None and intent.dates.start_date and intent.dates.end_date:
        count = (intent.dates.end_date - intent.dates.start_date).days + 1
    if not count or count > 30:
        raise PlanningError("duration_required_or_exceeds_30_days", 422)
    by_id = {p.place_id: p for p in state.candidates}
    excluded = {s.place_id for s in state.selections if s.decision == "exclude"}
    excluded |= {c.place_id for c in intent.constraints if c.kind == "exclude" and c.strength == "hard"}
    required = {s.place_id for s in state.selections if s.decision in {"include", "lock"}}
    required |= {c.place_id for c in intent.constraints if c.kind in {"must_visit", "reservation", "booked_hotel"} and c.strength == "hard"}
    if required & excluded:
        raise PlanningError("locked_or_required_place_is_excluded", 422)
    if not required <= set(by_id):
        raise PlanningError("locked_places_require_refresh", 422)
    groups = identity_groups([p for p in state.candidates if p.category == "attraction" and p.place_id not in excluded and p.coordinates])
    attractions, children = [], {}
    mapped_required, fixed = set(), {}
    for root, members in groups.items():
        main = by_id[root] if root in by_id else members[0]
        attractions.append(main)
        children[main.place_id] = [p.place_id for p in members if p.place_id != main.place_id]
        if any(p.place_id in required for p in members):
            mapped_required.add(main.place_id)
        dates = [s.date for s in state.selections if s.place_id in {p.place_id for p in members} and s.date]
        dates += [c.time_window.start.date() for c in intent.constraints if c.kind == "reservation" and c.place_id in {p.place_id for p in members}]
        if dates and intent.dates.start_date:
            if len(set(dates)) > 1:
                raise PlanningError("conflicting_parent_child_visit_dates", 422)
            fixed[main.place_id] = (dates[0] - intent.dates.start_date).days + 1
    if not attractions:
        raise PlanningError("attraction_candidates_required", 422)
    excluded_food = {s.place_id for s in state.selections if s.decision == "exclude"}
    food_pool = [p for p in state.candidates if p.category == "restaurant" and p.coordinates and p.place_id not in excluded_food]
    supported = [p for p in attractions if p.place_id in mapped_required or
        any(meters(p, food) <= 2000 for food in food_pool)]
    if len(supported) >= count:
        attractions = supported
    per_day = 3 if intent.preferences.compact_nearby else (2 if intent.preferences.pace == "relaxed" or "舒适" in intent.preferences.comfort_tags else 3)
    scores = {p.place_id: preference_score(p, intent) for p in attractions}
    assignments = allocate(attractions, count, per_day, mapped_required, fixed, scores,
        spread_seeds=bool(intent.preferences.compact_nearby))
    # Preserve untouched day assignments when editing a previous draft. Changes
    # enter as explicit assignments, then routes/times are recomputed.
    if previous and not intent.preferences.compact_nearby:
        retained = []
        assignments = []
        for day in previous.days:
            group = []
            for stop in day.stops:
                place = by_id.get(stop.place_id)
                if stop.category != "attraction" or not place or place.place_id in excluded:
                    continue
                if place.place_id in {p.place_id for p in retained} or any(same_visit_area(place, p) for p in retained):
                    continue
                retained.append(place)
                group.append(place)
            assignments.append(group)
    hotel_ranks = hotel_ranking([p for p in state.candidates if p.place_id not in excluded], assignments, intent)
    forced_hotels = [by_id[pid] for pid in required if by_id[pid].category == "hotel"]
    if len(forced_hotels) > 1:
        raise PlanningError("multiple_locked_hotels_require_explicit_stay_assignment", 422)
    # Retain an explicit replace_hotel edit, but re-rank an unselected hotel
    # after other edits instead of carrying a distant old default forever.
    edited_hotel_id = (previous.days[0].hotel_place_id if previous and previous.days and
        state.current_plan and state.current_plan.days and
        previous.days[0].hotel_place_id != state.current_plan.days[0].hotel_place_id else None)
    hotel = forced_hotels[0] if forced_hotels else (by_id.get(edited_hotel_id) if edited_hotel_id else
        (by_id[hotel_ranks[0].place_id] if hotel_ranks else None))
    food_candidates = [p for p in state.candidates if p.category == "restaurant" and p.place_id not in excluded and p.coordinates]
    # Record meal coverage separately from routing. Missing restaurant candidates
    # must not suppress real routes between places that are already known.
    retrieval_gaps = []
    for group in assignments:
        nearby = [{food.place_id for food in food_candidates if meters(food, anchor) <= 2000} for anchor in group]
        retrieval_gaps.extend(anchor.place_id for anchor, found in zip(group, nearby) if not found)
        if group and len(set().union(*nearby)) < 2:
            retrieval_gaps.extend(anchor.place_id for anchor in group)
    retrieval_gaps = list(dict.fromkeys(retrieval_gaps))
    prior_legs = [r for d in state.current_plan.days for r in d.routes] if state.current_plan else []
    router = RoutePlanner(provider, intent, route_limit, prior_legs)
    # The route budget belongs to the legs shown in the final itinerary. Hotel
    # candidates have already been ranked against all days by proximity;
    # probing alternatives here previously consumed calls before later days.
    requested_modes = set(intent.preferences.intercity_modes)
    transfer_offers = [offer for offer in state.travel_offers if not requested_modes or offer.get("category") in requested_modes]
    transfers = choose_transfers(transfer_offers, intent, state.candidates)
    days, food_ranks, used_restaurants = [], [], set()
    version = max([p.version for p in state.plan_history], default=0) + 1
    for i, assigned in enumerate(assignments, 1):
        date = intent.dates.start_date + timedelta(days=i - 1) if intent.dates.start_date else None
        if assigned:
            # Include the actual arrival/departure station in the shortlist
            # geometry. Only the chosen order consumes directed map calls.
            start = transfers["outbound"]["place"] if i == 1 and "outbound" in transfers else hotel
            end = transfers["inbound"]["place"] if i == count and "inbound" in transfers else hotel
            assigned = order_day_visits(assigned, start, end)
        restaurants = []
        for anchor in ([assigned[0], assigned[-1]] if assigned else []):
            ranks = nearby_ranking([p for p in state.candidates if p.place_id not in excluded
                and p.place_id not in {r.place_id for r in restaurants}], [anchor], "restaurant", intent)
            # Diversity is a tie-break within a local shortlist, never a reason
            # to cross the city for an unused restaurant.
            local = [r for r in ranks if meters(by_id[r.place_id], anchor) <= 2000]
            pool = local
            if pool:
                chosen = next((r for r in pool if r.place_id not in used_restaurants), pool[0])
                food_ranks.append(chosen)
                restaurants.append(by_id[chosen.place_id])
                used_restaurants.add(chosen.place_id)
        day = schedule(i, date, assigned, hotel, restaurants, router, intent, children,
            arrival=transfers.get("outbound") if i == 1 else None,
            departure=transfers.get("inbound") if i == count else None)
        for stop in day.stops:
            stop.locked = stop.place_id in required or bool(set(stop.child_place_ids) & required)
        days.append(day)
    missing = ["开放时间与预约规则", "餐馆菜单与饮食偏好匹配", "酒店日期房价与余房", "完整费用覆盖"]
    if not intent.dates.start_date:
        missing.append("出发日期")
    if not intent.budget.money:
        missing.append("预算")
    if intent.origin and intent.origin.label != intent.destination.label and len(transfers) != 2:
        missing.append("往返航班班次、票价及机场接驳" if "flight" in requested_modes else "往返城际交通与抵离接驳")
    return ItineraryDraft(plan_id=state.current_plan_ref.plan_id if state.current_plan_ref else str(uuid4()),
        version=version, base_state_version=state.state_version, intent_snapshot_ref=intent.intent_id,
        status="partial", days=days, recommendations=hotel_ranks[:5] + list({r.place_id: r for r in food_ranks}.values()),
        cost_summary=summarize_costs(days, by_id, intent),
        assumptions=["分组与距离初筛使用地点坐标；路线耗时全部来自地图服务", "每天可用时段待抵离交通确认", "未推断房间数量、房价或余房"],
        missing_requirements=missing, supplier_status={"amap": "route_estimates", **state.supplier_status},
        travel_offers=state.travel_offers, selected_offer_ids=[t["offer_id"] for t in transfers.values()],
        retrieval_gaps=retrieval_gaps)
