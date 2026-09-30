from math import asin, cos, radians, sin, sqrt
import re
import unicodedata


def meters(a, b):
    """Geodesic distance for pruning/ranking only, never reported as route duration."""
    if not a.coordinates or not b.coordinates or a.coordinates.crs != b.coordinates.crs:
        return float("inf")
    x, y = a.coordinates, b.coordinates
    dlat, dlon = radians(y.latitude - x.latitude), radians(y.longitude - x.longitude)
    h = sin(dlat / 2) ** 2 + cos(radians(x.latitude)) * cos(radians(y.latitude)) * sin(dlon / 2) ** 2
    return 6371000 * 2 * asin(sqrt(min(1, h)))


def same_visit_area(a, b):
    """A repeated POI or two local points inside one named attraction complex."""
    def key(name):
        return "".join(char for char in unicodedata.normalize("NFKC", name).casefold()
                       if char.isalnum())
    def complex_key(name):
        root = re.split(r"[-—·]", unicodedata.normalize("NFKC", name), maxsplit=1)[0].strip()
        return key(root) if root.endswith(("公园", "景区", "博物馆", "博物院", "遗址", "寺", "宫", "园")) else None
    if a.category != "attraction" or b.category != "attraction":
        return False
    distance = meters(a, b)
    return ((key(a.name) == key(b.name) and distance <= 1500) or
            (complex_key(a.name) and complex_key(a.name) == complex_key(b.name) and distance <= 2000))


def identity_groups(places):
    by_id = {p.place_id: p for p in places}
    parent = {}
    for place in places:
        for relation in place.relations:
            if relation.kind in {"inside", "same_identity"} and relation.place_id in by_id:
                if relation.kind == "inside":
                    parent[place.place_id] = relation.place_id
                else:
                    parent[max(place.place_id, relation.place_id)] = min(place.place_id, relation.place_id)
        fact = place.facts.get("child_place_ids")
        for child in fact.value if fact and isinstance(fact.value, list) else []:
            if child in by_id and child != place.place_id:
                parent[child] = place.place_id
    # Name + short distance supplies a *candidate* containment relation only for
    # explicit main-name/sub-name forms; proximity alone never merges identities.
    for child in places:
        for main in places:
            if child.place_id == main.place_id or main.category != "attraction":
                continue
            if same_visit_area(child, main) and child.place_id > main.place_id:
                parent.setdefault(child.place_id, main.place_id)
            root = main.name.removesuffix("博物馆").removesuffix("博物院")
            if len(root) >= 3 and child.name.startswith(root + "-") and meters(main, child) < 1500:
                parent.setdefault(child.place_id, main.place_id)
    def root(pid):
        visited = set()
        while pid in parent and pid not in visited:
            visited.add(pid)
            pid = parent[pid]
        return min(visited) if pid in visited and visited else pid
    groups = {}
    for place in places:
        groups.setdefault(root(place.place_id), []).append(place)
    return groups


def allocate(places, count, per_day, required_ids, fixed_days, scores):
    days = [[] for _ in range(count)]
    remaining = list(places)
    for place in list(remaining):
        index = fixed_days.get(place.place_id)
        if index is not None and 1 <= index <= count:
            days[index - 1].append(place)
            remaining.remove(place)
    # Penalize isolated optional attractions before selecting the finite pool.
    # Explicitly required nodes always remain, even if they imply a long trip.
    center = min(places, key=lambda p: sum(meters(p, q) for q in places)) if places else None
    ordered = sorted(remaining, key=lambda p: (p.place_id not in required_ids,
        -(scores[p.place_id] - (meters(p, center) / 10000 if center else 0)), p.place_id))
    remaining = ordered[:max(count * per_day - sum(map(len, days)), len(required_ids & {p.place_id for p in ordered}))]
    for index, day in enumerate(days):
        if not day and remaining:
            seed = max(remaining, key=lambda p: meters(p, center))
            day.append(seed)
            remaining.remove(seed)
        # Fill a compact area before seeding another day, while reserving at
        # least one attraction for each remaining empty day.
        empty_later = sum(not d for d in days[index + 1:])
        while day and len(day) < per_day and len(remaining) > empty_later:
            neighbour = min(remaining, key=lambda p: min(meters(p, q) for q in day))
            day.append(neighbour)
            remaining.remove(neighbour)
    while remaining:
        place = remaining.pop(0)
        possible = [i for i, day in enumerate(days) if len(day) < per_day]
        if not possible:
            if place.place_id in required_ids:
                possible = list(range(count))
            else:
                continue
        chosen = min(possible, key=lambda i: (min((meters(place, p) for p in days[i]), default=0), len(days[i])))
        days[chosen].append(place)
    return days
