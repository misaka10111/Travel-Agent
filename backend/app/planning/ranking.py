from math import isfinite
from statistics import median

from app.planning.clustering import meters
from app.schemas.itinerary import Recommendation


def preference_score(place, intent):
    tags = place.facts.get("tag")
    text = place.name + " " + (str(tags.value) if tags else "") + " " + str(place.facts.get("type").value if place.facts.get("type") else "")
    words = intent.preferences.interests + intent.preferences.must_visit_names
    cultural = any(w in " ".join(words) for w in ("人文", "历史", "古代建筑", "古建"))
    score = sum(1 for word in words if word in text) + (2 if place.name in intent.preferences.must_visit_names else 0)
    if cultural:
        score += sum(term in text for term in ("博物", "文物", "古建", "寺", "庙", "宫", "古迹"))
    if "-" in place.name:
        score -= 3  # Prefer whole attractions over entrances/internal lookout points.
    return score


def nearby_ranking(places, anchors, category, intent):
    result = []
    for place in places:
        if place.category != category or not place.coordinates or not anchors:
            continue
        distances = [meters(place, anchor) for anchor in anchors]
        distance = sum(distances) / len(distances) if category == "hotel" else min(distances)
        rating = place.facts.get("rating")
        try:
            quality = min(1, max(0, float(rating.value) / 5)) if rating else 0
        except (ValueError, TypeError):
            quality = 0
        components = {"proximity": 1 / (1 + distance / 1000), "provider_rating": quality}
        if category == "restaurant":
            tag = place.facts.get("tag")
            text = str(tag.value) if tag else ""
            matches = sum(word in text for word in intent.preferences.dietary_preferences)
            components["explicit_dietary_tag_match"] = float(matches)
            if any("辣" in p and ("不" in p or "少" in p) for p in intent.preferences.dietary_preferences) and any(w in text for w in ("麻辣", "香辣", "辣味")):
                components["spicy_tag_penalty"] = -2.0
        unknown = ["日期房价与余房待查询"] if category == "hotel" else ["清淡/不辣菜单与营业时段待核实"]
        result.append(Recommendation(place_id=place.place_id, category=category, score_components=components,
            reasons=["按多天景点平均距离初筛；最终路线需核验" if category == "hotel" else "按用餐区域附近筛选；距离不是通勤耗时"],
            unknowns=unknown))
    return sorted(result, key=lambda r: (-sum(r.score_components.values()), r.place_id))


def hotel_ranking(places, day_groups, intent):
    """Choose a stable trip base using each day's attractions equally.

    Distances only shortlist candidate locations; actual commute time is checked
    after routing. Rating is a small tie-breaker, not a substitute for proximity.
    """
    groups = [group for group in day_groups if group]
    if not groups:
        return []
    anchors = [place for group in groups for place in group]
    ranked = nearby_ranking(places, anchors, "hotel", intent)
    by_id = {place.place_id: place for place in places}
    comparable = []
    for item in ranked:
        hotel = by_id[item.place_id]
        daily_meters = [sum(meters(hotel, place) for place in group) / len(group) for group in groups]
        if not all(isfinite(distance) for distance in daily_meters):
            continue
        average = sum(daily_meters) / len(daily_meters)
        worst = max(daily_meters)
        # A single required outlying day should not pull the base away from
        # the main stay area used on most days.
        distance_proxy = median(daily_meters) * 0.7 + average * 0.3
        quality = item.score_components["provider_rating"]
        item.score_components = {"multi_day_proximity": 1 / (1 + distance_proxy / 1000),
            "provider_rating": quality * 0.02}
        item.reasons = [f"距各日景点的直线距离平均约{average / 1000:.1f}公里，最远一天约{worst / 1000:.1f}公里；实际通勤以地图路线为准"]
        comparable.append(item)
    return sorted(comparable, key=lambda item: (-sum(item.score_components.values()), item.place_id))
