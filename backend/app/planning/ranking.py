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
