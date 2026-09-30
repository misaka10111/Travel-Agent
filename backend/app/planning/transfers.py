"""Only station identities and coherent supplier times can constrain arrival days."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.schemas.common import utc_now
from app.providers.maps.base import MapError


def choose_transfers(offers, intent, places):
    by_id = {p.place_id: p for p in places}
    result = {}
    if not intent.dates.start_date:
        return result
    zone = ZoneInfo(intent.dates.timezone or intent.destination.timezone or "Asia/Shanghai")
    for direction in ("outbound", "inbound"):
        options = []
        for offer in offers:
            if offer.get("direction") != direction or offer.get("category") not in {"train", "flight"}:
                continue
            segments = offer.get("segments") or []
            pid = offer.get("destination_place_id" if direction == "outbound" else "origin_place_id")
            if not segments or pid not in by_id:
                continue
            try:
                observed = datetime.fromisoformat(offer["observed_at"])
                if not observed.tzinfo or not 0 <= (utc_now() - observed).total_seconds() <= 3600:
                    continue
                times = [(datetime.fromisoformat(s["departure_at"]), datetime.fromisoformat(s["arrival_at"])) for s in segments]
                if any(not dep.tzinfo or not arr.tzinfo or arr <= dep for dep, arr in times):
                    continue
                if any(times[i][1] > times[i + 1][0] for i in range(len(times) - 1)):
                    continue
                event = (times[-1][1] if direction == "outbound" else times[0][0]).astimezone(zone)
            except (KeyError, TypeError, ValueError):
                continue
            expected = intent.dates.start_date if direction == "outbound" else intent.dates.end_date
            if direction == "inbound" and expected is None and intent.dates.duration_days:
                expected = intent.dates.start_date + timedelta(days=intent.dates.duration_days - 1)
            if expected is None:
                continue
            if expected and event.date() != expected:
                continue
            duration = (times[-1][1] - times[0][0]).total_seconds()
            # Prefer daylight usable time and fewer connections; keep selection
            # provisional and visible, never imply a booked or cheapest ticket.
            score = (len(segments), duration, event.hour if direction == "outbound" else -event.hour)
            buffer = 120 if offer["category"] == "flight" else 60
            options.append((score, {"place": by_id[pid], "minute": event.hour * 60 + event.minute,
                "buffer": buffer, "offer_id": offer["offer_id"], "date": event.date()}))
        if options:
            result[direction] = min(options, key=lambda p: p[0])[1]
    return result


def match_offers(offers, candidates, provider, region):
    """Exact station names need category+city; hotels need name+address, never name alone."""
    def clean(value):
        return (value or "").replace(" ", "").replace("（", "(").replace("）", ")")
    found = {p.place_id: p for p in candidates}
    lookups = {}
    for offer in offers:
        if offer["category"] == "hotel":
            matching = [p for p in candidates if p.category == "hotel" and clean(p.name) == clean(offer.get("name"))
                and p.address and offer.get("address") and clean(p.address) == clean(offer["address"])]
            if len(matching) == 1:
                offer["map_place_id"], offer["identity_match"] = matching[0].place_id, "matched"
            continue
        segments = offer.get("segments") or []
        if not segments:
            continue
        outbound = offer["direction"] == "outbound"
        label = segments[-1].get("destination_station") if outbound else segments[0].get("origin_station")
        if not label:
            continue
        if label not in lookups:
            if len(lookups) >= 4:
                continue
            try:
                places = provider.search_places(label, region, 5)
            except MapError:
                # Retain supplier observations without claiming station identity.
                break
            matches = [p for p in places if p.category == "transport" and p.coordinates and clean(p.name).removesuffix("站") == clean(label).removesuffix("站")
                and p.region.country_code == region.country_code and p.region.label.removesuffix("市") == region.label.removesuffix("市")]
            lookups[label] = matches[0] if len(matches) == 1 else None
        place = lookups[label]
        if place:
            found[place.place_id] = place
            offer["destination_place_id" if outbound else "origin_place_id"] = place.place_id
            offer["identity_match"] = "matched"
    return list(found.values())
