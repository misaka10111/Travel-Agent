"""Explicit bounded probes. Persist only test metadata, never keys/raw responses/geometry."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.providers.maps.amap import AmapProvider
from app.providers.maps.base import MapError, RouteQuery
from app.providers.maps.google import GoogleProvider
from app.providers.maps.policy import get_policy
from app.providers.maps.transport import MapTransport
from app.schemas.common import RegionRef, utc_now

CITIES = {
    "shanghai": {"label": "上海", "country_code": "CN", "timezone": "Asia/Shanghai",
        "queries": [("豫园", ["豫园", "上海豫园"]), ("上海城隍庙", ["上海城隍庙"]), ("外滩", ["外滩"])]},
    "singapore": {"label": "Singapore", "country_code": "SG", "timezone": "Asia/Singapore",
        "queries": [("Merlion Park", ["Merlion Park"]), ("Gardens by the Bay", ["Gardens by the Bay"]),
                    ("Singapore Botanic Gardens", ["Singapore Botanic Gardens"])]},
}


def run_probe(provider_name, city_name, *, max_calls=None, departure_at=None):
    settings = Settings(_env_file=BACKEND / ".env")
    limit = settings.map_probe_max_calls if max_calls is None else min(max_calls, settings.map_probe_max_calls)
    transport = MapTransport(max_calls=limit, timeout_seconds=settings.map_request_timeout_seconds)
    provider = AmapProvider(settings.amap_web_service_key, transport) if provider_name == "amap" else GoogleProvider(settings.google_maps_api_key, transport)
    city = CITIES[city_name]
    region = RegionRef(label=city["label"], country_code=city["country_code"], timezone=city["timezone"], identity_status="confirmed")
    departure = departure_at or (datetime.now(ZoneInfo(city["timezone"])).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1))
    report = {"probe_version": "1.0", "provider": provider_name, "city": city_name,
        "started_at": utc_now().isoformat(), "requested_departure_at": departure.isoformat(), "max_calls": limit,
        "success_criteria": "Valid outcomes including explicit no_route; at least one ok route per mode, with geometry for each ok route.",
        "policy": get_policy(provider_name).model_dump(), "checks": [], "status": "partial"}
    places = []
    fatal = False

    def check(kind, label, operation, summarize):
        nonlocal fatal
        start = perf_counter()
        try:
            result = operation()
            summary = summarize(result)
            valid = all(summary.get(key, True) for key in ("has_identity_and_coordinates", "same_identity", "has_coordinates"))
            report["checks"].append({"kind": kind, "label": label, "status": "passed" if valid else "failed", **summary,
                "latency_ms": round((perf_counter() - start) * 1000)})
            return result
        except MapError as exc:
            report["checks"].append({"kind": kind, "label": label, "status": "blocked" if exc.code in ("missing_credentials", "permission_denied", "unsupported") else "failed",
                "error": exc.summary(), "latency_ms": round((perf_counter() - start) * 1000)})
            fatal = exc.code in {"missing_credentials", "permission_denied", "rate_limited", "budget_exhausted", "unsupported"}
            return None

    try:
        for query, accepted in city["queries"]:
            candidates = check("search", query, lambda: provider.search_places(query, region),
                lambda values: {"candidate_count": len(values), "has_identity_and_coordinates": any(p.coordinates and p.provider_refs for p in values)})
            if fatal:
                break
            # Exact fixture name, a single match and coordinates. No first-result guessing.
            matches = [p for p in candidates or [] if p.name in accepted and p.category == "attraction" and p.coordinates]
            if len(matches) == 1:
                places.append(matches[0])
            else:
                report["checks"].append({"kind": "identity", "label": query, "status": "failed",
                    "reason": "exact_sample_match_missing_or_ambiguous", "match_count": len(matches)})
        if places and not fatal:
            ref = places[0].provider_refs[0]
            check("detail", "first_sample", lambda: provider.get_place(ref.provider_place_id, region),
                lambda place: {"same_identity": place.place_id == places[0].place_id, "has_coordinates": place.coordinates is not None})
        if len(places) == 3 and not fatal:
            if provider_name == "amap":
                check("nearby", "restaurants_near_first_sample", lambda: provider.nearby_places(places[0].coordinates, "餐厅", region),
                    lambda values: {"candidate_count": len(values)})
            for a, b in ((places[0], places[1]), (places[1], places[2])):
                if fatal:
                    break
                for mode in ("walking", "transit", "driving"):
                    query = RouteQuery(origin=a, destination=b, mode=mode, departure_at=departure)
                    leg = check("route", f"leg_{places.index(a) + 1}_{mode}", lambda: provider.route(query),
                        lambda leg: {"mode": leg.mode, "route_status": leg.status, "duration_present": leg.duration_seconds is not None,
                            "distance_present": leg.distance_meters is not None, "geometry_present": leg.geometry is not None,
                            "departure_time_applied": leg.departure_time_applied, "warnings": leg.warnings,
                            "unknown_reason": leg.unknown_reason})
                    if leg is not None and (leg.status not in ("ok", "no_route") or (leg.status == "ok" and leg.geometry is None)):
                        report["checks"][-1]["status"] = "failed"
                    if fatal:
                        break
        route_checks = [row for row in report["checks"] if row["kind"] == "route"]
        report["mode_coverage"] = {mode: any(row.get("mode") == mode and row.get("route_status") == "ok" for row in route_checks)
            for mode in ("walking", "transit", "driving")}
        if len(route_checks) == 6 and all(report["mode_coverage"].values()) and all(row["status"] == "passed" for row in report["checks"]):
            report["status"] = "passed"
        elif fatal:
            report["status"] = "blocked"
    finally:
        report["calls_used"] = transport.calls_used
        report["finished_at"] = utc_now().isoformat()
        transport.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("amap", "google"), required=True)
    parser.add_argument("--city", choices=tuple(CITIES), required=True)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--departure-at", help="ISO timestamp with UTC offset; default tomorrow 10:00 local")
    parser.add_argument("--report", type=Path, help="Optional JSON file containing only sanitized test metadata")
    args = parser.parse_args()
    if args.max_calls is not None and not 1 <= args.max_calls <= 30:
        parser.error("--max-calls must be between 1 and 30")
    departure = None
    if args.departure_at:
        try:
            departure = datetime.fromisoformat(args.departure_at)
            if departure.tzinfo is None or departure.utcoffset() is None:
                raise ValueError()
        except ValueError:
            parser.error("--departure-at requires an ISO timestamp with UTC offset")
    report = run_probe(args.provider, args.city, max_calls=args.max_calls, departure_at=departure)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(payload)
    return 0 if report["status"] == "passed" else 2 if report["status"] == "blocked" else 3


if __name__ == "__main__":
    raise SystemExit(main())
