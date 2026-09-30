"""Bounded adapters for the installed FlyAI CLI and Tavily evidence search.

Supplier responses stay in memory; errors never include raw stdout/credentials.
All segments are retained, unlike the legacy first-segment-only wrapper.
"""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.schemas.common import utc_now
from app.schemas.place import Fact
from app.schemas.travel import TravelOffer


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))).isoformat()
    except ValueError:
        return None


def normalize_offers(data, category, direction):
    if not isinstance(data, dict) or data.get("status") not in (0, None):
        return [], "supplier_rejected"
    container = data.get("data") or {}
    items = container.get("itemList") if isinstance(container, dict) else None
    if not isinstance(items, list):
        return [], "invalid_supplier_contract"
    offers = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        segments = []
        journeys = item.get("journeys") or []
        if not isinstance(journeys, list):
            continue
        for journey in journeys:
            if not isinstance(journey, dict) or not isinstance(journey.get("segments", []), list):
                continue
            for segment in journey.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                segments.append({"service_no": segment.get("marketingTransportNo"),
                    "origin_city": segment.get("depCityName"), "destination_city": segment.get("arrCityName"),
                    "origin_station": segment.get("depStationName"), "destination_station": segment.get("arrStationName"),
                    "departure_at": _time(segment.get("depDateTime")), "arrival_at": _time(segment.get("arrDateTime")),
                    "seat": segment.get("seatClassName")})
        price = item.get("price", item.get("ticketPrice", item.get("adultPrice")))
        url = item.get("detailUrl", item.get("jumpUrl"))
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            url = None
        candidate = {"category": category, "direction": direction, "name": item.get("name"),
            "supplier_offer_id": str(item.get("id") or item.get("shId")) if item.get("id") or item.get("shId") else None,
            "address": item.get("address"), "segments": segments,
            "price_display": str(price) if price is not None else None,
            "currency": item.get("currency") if isinstance(item.get("currency"), str) and len(item["currency"]) == 3 else None,
            "price_scope": "unknown", "observed_at": utc_now().isoformat(), "url": url,
            "identity_match": "unresolved", "availability": "supplier_observed_not_booked"}
        try:
            offers.append(TravelOffer.model_validate(candidate).model_dump(mode="json"))
        except ValidationError:
            continue
    return offers, "observed" if offers else "no_results"


class TravelServices:
    def __init__(self, settings, runner=None):
        self.settings = settings
        self.runner = runner or self._run

    def _run(self, args):
        entry = Path(__file__).resolve().parents[3] / "SearchAgent/node_modules/@fly-ai/flyai-cli/dist/flyai-bundle.cjs"
        node = shutil.which("node")
        if not node or not entry.is_file():
            return {"status": -1}
        try:
            result = subprocess.run([node, str(entry), *args], capture_output=True, encoding="utf-8",
                timeout=self.settings.travel_supplier_timeout_seconds, shell=False)
            return json.loads(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {"status": -1}

    def query(self, intent, categories, reserve):
        if not intent.dates.start_date:
            return [], {c: "dates_required" for c in categories}
        if not self.settings.travel_supplier_enabled:
            return [], {c: "disabled_pending_account_validation" for c in categories}
        offers, statuses = [], {}
        start = intent.dates.start_date
        end = intent.dates.end_date
        if not end and intent.dates.duration_days:
            from datetime import timedelta
            end = start + timedelta(days=intent.dates.duration_days - 1)
        for category in categories:
            if category not in self.settings.travel_supplier_categories:
                statuses[category] = "category_disabled"
                continue
            requests = []
            if category == "hotel":
                if not end or end <= start:
                    statuses[category] = "overnight_dates_required"
                    continue
                requests = [("stay", ["search-hotel", "--dest-name", intent.destination.label,
                    "--check-in-date", str(start), "--check-out-date", str(end)])]
            else:
                if not intent.origin or not end:
                    statuses[category] = "origin_and_return_date_required"
                    continue
                requests = [(direction, ["search-" + category, "--origin", origin, "--destination", destination,
                    "--dep-date", str(day), "--journey-type", "1"])
                    for direction, origin, destination, day in [
                        ("outbound", intent.origin.label, intent.destination.label, start),
                        ("inbound", intent.destination.label, intent.origin.label, end)]]
            observations = []
            for direction, args in requests:
                reserve()
                found, status = normalize_offers(self.runner(args), category, direction)
                # Do not accept a returned connection to the wrong destination.
                if category != "hotel":
                    target = intent.destination.label if direction == "outbound" else intent.origin.label
                    source = intent.origin.label if direction == "outbound" else intent.destination.label
                    requested = start if direction == "outbound" else end
                    found = [o for o in found if o["segments"]
                        and (o["segments"][-1].get("destination_city") or "").removesuffix("市") == target.removesuffix("市")
                        and (o["segments"][0].get("origin_city") or "").removesuffix("市") == source.removesuffix("市")
                        and (o["segments"][0].get("departure_at") or "").startswith(str(requested))]
                    status = status if found or status != "observed" else "destination_unconfirmed"
                for offer in found:
                    offer["query_start_date"] = str(start if direction != "inbound" else end)
                    offer["query_end_date"] = str(end) if category == "hotel" else None
                offers.extend(found)
                observations.append(status)
            statuses[category] = ",".join(observations)
        return offers, statuses

    def evidence(self, places, topic, reserve):
        key = self.settings.tavily_api_key
        if not key or not key.get_secret_value():
            return places
        enriched = []
        for place in places:
            reserve()
            result = place.model_copy(deep=True)
            try:
                with httpx.Client(timeout=15, follow_redirects=False) as client:
                    response = client.post("https://api.tavily.com/search", headers={"Authorization": "Bearer " + key.get_secret_value()}, json={
                        "query": f"{place.region.label} {place.name} " + ("官网 开放时间 预约" if topic == "opening_hours" else "菜单 清淡 不辣"),
                        "max_results": 3, "search_depth": "basic", "include_answer": False})
                    if response.status_code != 200:
                        raise ValueError()
                    rows = response.json().get("results")
                    if not isinstance(rows, list):
                        raise ValueError()
                    evidence = [{"url": r["url"], "title": str(r.get("title", ""))[:200], "excerpt": str(r.get("content", ""))[:800]}
                        for r in rows if isinstance(r, dict) and isinstance(r.get("url"), str) and urlsplit(r["url"]).scheme == "https"]
                    result.facts["web_" + topic] = Fact(value=evidence, source_ref="tavily:search", observed_at=utc_now())
            except (httpx.RequestError, ValueError, TypeError):
                result.facts["web_" + topic] = Fact(value=None, source_ref="tavily:search", observed_at=utc_now(), unknown_reason="evidence_service_unavailable")
            enriched.append(result)
        return enriched
