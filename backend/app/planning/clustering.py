from itertools import permutations
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


def area_key(place):
    """Named large complex for day grouping, not a POI identity equivalence."""
    name = unicodedata.normalize("NFKC", place.name).strip()
    root = re.split(r"[-—·]", name, maxsplit=1)[0].strip()
    city = place.region.label
    if city and root.startswith(city):
        root = root[len(city):]
    for suffix in ("风景名胜区", "风景区", "景区"):
        if root.endswith(suffix):
            return root[:-len(suffix)] or None
    return None


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


def allocate(places, count, per_day, required_ids, fixed_days, scores, spread_seeds=False):
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
    capacity = max(count * per_day - sum(map(len, days)), len(required_ids & {p.place_id for p in ordered}))
    groups = {}
    for place in ordered:
        groups.setdefault(area_key(place) or place.place_id, []).append(place)
    remaining = [place for place in ordered if place.place_id in required_ids]
    while len(remaining) < capacity and any(groups.values()):
        for group in groups.values():
            while group and group[0] in remaining:
                group.pop(0)
            if group and len(remaining) < capacity:
                remaining.append(group.pop(0))
    for index, day in enumerate(days):
        if not day and remaining:
            seeded = [place for group in days for place in group]
            occupied_areas = {area_key(place) for place in seeded if area_key(place)}
            distinct = [place for place in remaining if not area_key(place) or area_key(place) not in occupied_areas]
            seed_pool = distinct or remaining
            if spread_seeds and seeded:
                seed = max(seed_pool, key=lambda p: (min(meters(p, other) for other in seeded), scores[p.place_id]))
            elif spread_seeds:
                seed = max(seed_pool, key=lambda p: scores[p.place_id])
            else:
                seed = max(seed_pool, key=lambda p: meters(p, center))
            day.append(seed)
            remaining.remove(seed)
        # Fill a compact area before seeding another day, while reserving at
        # least one attraction for each remaining empty day.
        empty_later = sum(not d for d in days[index + 1:])
        while day and len(day) < per_day and len(remaining) > empty_later:
            same_area = [p for p in remaining if area_key(p) and area_key(p) in {area_key(q) for q in day}]
            unrepresented = {area_key(p) for p in remaining if area_key(p) and
                not any(area_key(q) == area_key(p) for group in days for q in group)}
            if not same_area and unrepresented and empty_later and len(unrepresented) <= empty_later:
                break
            neighbour = min(same_area or remaining, key=lambda p: min(meters(p, q) for q in day))
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
        key = area_key(place)
        same_area_days = [i for i in possible if key and any(area_key(p) == key for p in days[i])]
        chosen = min(same_area_days or possible, key=lambda i: (min((meters(place, p) for p in days[i]), default=0), len(days[i])))
        days[chosen].append(place)
    return days


def order_day_visits(places, start=None, end=None):
    """Choose a short visit order without spending route API calls.

    The endpoints are the actual arrival/departure station when known, otherwise
    the selected hotel. This is a straight-line shortlist heuristic; schedule()
    still obtains directed travel times from the map provider.
    """
    if len(places) < 2:
        return list(places)

    def length(path):
        chain = ([start] if start else []) + list(path) + ([end] if end else [])
        return sum(meters(a, b) for a, b in zip(chain, chain[1:]))

    # Normal days have 2-3 attractions. A bounded exact search also handles
    # extra explicitly required visits without an all-pairs map request.
    if len(places) <= 6:
        return list(min(permutations(places), key=lambda path: (length(path), tuple(p.place_id for p in path))))

    remaining = list(places)
    ordered = []
    cursor = start or remaining[0]
    while remaining:
        chosen = min(remaining, key=lambda p: (meters(cursor, p), p.place_id))
        ordered.append(chosen)
        remaining.remove(chosen)
        cursor = chosen
    # For unusually large required groups, improve the return leg as well.
    for _ in range(len(ordered)):
        best = ordered
        best_length = length(best)
        for i in range(len(ordered) - 1):
            for j in range(i + 1, len(ordered)):
                candidate = ordered[:i] + list(reversed(ordered[i:j + 1])) + ordered[j + 1:]
                cost = length(candidate)
                if cost < best_length:
                    best, best_length = candidate, cost
        if best is ordered:
            break
        ordered = best
    return ordered
