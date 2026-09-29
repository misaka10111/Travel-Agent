"""Amap v5 POI and direction adapters, scoped to mainland CN for P0."""

from datetime import timezone, timedelta
from uuid import NAMESPACE_URL, uuid5

from pydantic import SecretStr, ValidationError

from app.providers.maps.base import MapError, ProviderCapabilities, RouteQuery, route_result, validate_endpoints, normalize_response_errors
from app.providers.maps.transport import MapTransport
from app.schemas.common import RegionRef
from app.schemas.place import Coordinates, Place, ProviderRef
from app.schemas.plan import RouteGeometry, RouteStep


def _text(value):
    return value if isinstance(value, str) and value else None


def _polyline_text(value):
    # Observed v5 transit shape: {"polyline": "lon,lat;lon,lat"}.
    return _text(value.get("polyline")) if isinstance(value, dict) else _text(value)


def _number(value):
    if value in (None, "", []) or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if value >= 0 and value < float("inf") else None


def _coordinates(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        longitude, latitude = value.split(",")
        return Coordinates(longitude=float(longitude), latitude=float(latitude), crs="GCJ02")
    except (ValueError, ValidationError):
        raise MapError("invalid_response", "Amap returned malformed coordinates.") from None


def _category(code: str):
    if code.startswith("11"):
        return "attraction"
    if code.startswith("05"):
        return "restaurant"
    if code.startswith("10"):
        return "hotel"
    if code.startswith("15"):
        return "transport"
    return "other"


class AmapProvider:
    capabilities = ProviderCapabilities(provider="amap", country_scope=["CN-mainland"],
        modes=["walking", "transit", "driving"], text_search=True, place_details=True, nearby_search=True)

    def __init__(self, key: SecretStr | None, transport: MapTransport):
        self._key = key
        self.transport = transport

    def _request(self, path: str, params: dict) -> dict:
        if not self._key or not self._key.get_secret_value():
            raise MapError("missing_credentials", "AMAP_WEB_SERVICE_KEY is not configured.")
        data = self.transport.request("GET", "https://restapi.amap.com" + path,
            params={**params, "key": self._key.get_secret_value()})
        if str(data.get("status")) != "1":
            code = str(data.get("infocode", "unknown"))
            code = code if len(code) == 5 and code.isdigit() else "unknown"
            if code in {"10003", "10004", "10010", "10014", "10019", "10020", "10021", "10044"}:
                raise MapError("rate_limited", "Amap quota or rate limit rejected the request.", retryable=True, provider_code=code)
            if code in {"10001", "10002", "10005", "10006", "10007", "10008", "10009", "10012", "10013"}:
                raise MapError("permission_denied", "Amap key, platform or service permission rejected the request.", provider_code=code)
            raise MapError("invalid_request", "Amap rejected the request parameters or service configuration.", provider_code=code)
        return data

    @staticmethod
    def _region(region: RegionRef):
        if region.country_code != "CN":
            raise MapError("unsupported", "Amap overseas permission and endpoint are not configured in P0.")

    @staticmethod
    def _place(item: dict, region: RegionRef) -> Place:
        if not isinstance(item, dict) or not _text(item.get("id")) or not _text(item.get("name")):
            raise MapError("invalid_response", "Amap POI is missing its ID or name.")
        coordinates = _coordinates(item.get("location"))
        # This deterministic ID identifies one provider entity, not a cross-provider merge.
        return Place(place_id=str(uuid5(NAMESPACE_URL, "amap:" + item["id"])), name=item["name"],
            category=_category(str(item.get("typecode", ""))), region=region,
            provider_refs=[ProviderRef(provider="amap", provider_place_id=item["id"])],
            coordinates=coordinates, coordinate_unknown_reason=None if coordinates else "provider_missing_coordinates",
            identity_status="candidate", address=_text(item.get("address")))

    @normalize_response_errors
    def search_places(self, query: str, region: RegionRef, limit: int = 5) -> list[Place]:
        self._region(region)
        if not query.strip() or not 1 <= limit <= 25:
            raise MapError("invalid_request", "Amap search requires a query and limit between 1 and 25.")
        data = self._request("/v5/place/text", {"keywords": query, "region": region.label,
            "city_limit": "true", "page_size": limit})
        pois = data.get("pois")
        if not isinstance(pois, list):
            raise MapError("invalid_response", "Amap search response is missing a POI list.")
        return [self._place(item, region) for item in pois]

    @normalize_response_errors
    def get_place(self, provider_place_id: str, region: RegionRef) -> Place:
        self._region(region)
        data = self._request("/v5/place/detail", {"id": provider_place_id})
        pois = data.get("pois")
        if not isinstance(pois, list) or not pois:
            raise MapError("invalid_response", "Amap detail response did not contain a POI.")
        if pois[0].get("id") != provider_place_id:
            raise MapError("invalid_response", "Amap detail returned a different POI ID.")
        return self._place(pois[0], region)

    @normalize_response_errors
    def nearby_places(self, location: Coordinates, query: str, region: RegionRef, *, radius_meters: int = 1000, limit: int = 5) -> list[Place]:
        self._region(region)
        if location.crs != "GCJ02" or not 0 < radius_meters <= 50000 or not 1 <= limit <= 25:
            raise MapError("invalid_request", "Amap nearby search needs GCJ02 coordinates and valid bounds.")
        data = self._request("/v5/place/around", {"location": self._coord_text(location),
            "keywords": query, "radius": radius_meters, "page_size": limit,
            "region": region.label, "city_limit": "true"})
        if not isinstance(data.get("pois"), list):
            raise MapError("invalid_response", "Amap nearby response is missing a POI list.")
        return [self._place(item, region) for item in data["pois"]]

    @staticmethod
    def _coord_text(location: Coordinates) -> str:
        return f"{location.longitude:.6f},{location.latitude:.6f}"

    @normalize_response_errors
    def route(self, query: RouteQuery):
        validate_endpoints(query, "GCJ02", "CN")
        params = {"origin": self._coord_text(query.origin.coordinates),
            "destination": self._coord_text(query.destination.coordinates), "show_fields": "cost,polyline"}
        transit = query.mode == "transit"
        if transit:
            # P0 is the Shanghai same-city trial. Do not invent city codes for other cities.
            if any(place.region.label not in ("上海", "上海市") for place in (query.origin, query.destination)):
                raise MapError("unsupported", "P0 Amap transit probe is configured for Shanghai only.")
            local = query.departure_at.astimezone(timezone(timedelta(hours=8)))
            params.update(city1="021", city2="021", date=local.strftime("%Y-%m-%d"),
                time=local.strftime("%H-%M"), AlternativeRoute="1")
        path = "transit/integrated" if transit else query.mode
        data = self._request("/v5/direction/" + path, params)
        container = data.get("route")
        if not isinstance(container, dict):
            raise MapError("invalid_response", "Amap route container is missing.")
        options = container.get("transits" if transit else "paths")
        if not isinstance(options, list):
            raise MapError("invalid_response", "Amap route options are missing.")
        if not options:
            return route_result(query, "amap", status="no_route", unknown_reason="provider_returned_no_route")
        route = options[0]
        cost = route.get("cost") or {}
        duration = _number(cost.get("duration"))
        distance = _number(route.get("distance"))
        if duration is None or distance is None:
            return route_result(query, "amap", status="unknown", unknown_reason="provider_missing_duration_or_distance")
        steps, points = [], []
        geometry_complete = True
        parts = [] if transit else list(route.get("steps") or [])
        if transit:
            for segment in route.get("segments") or []:
                if segment.get("railway") or segment.get("taxi"):
                    geometry_complete = False
                walking = segment.get("walking") or {}
                if walking:
                    steps.append(RouteStep(mode="walking", duration_seconds=_number((walking.get("cost") or {}).get("duration")),
                        distance_meters=_number(walking.get("distance"))))
                parts.extend(walking.get("steps") or [])
                for line in (segment.get("bus") or {}).get("buslines") or []:
                    steps.append(RouteStep(mode="transit", line_name=_text(line.get("name")),
                        duration_seconds=_number((line.get("cost") or {}).get("duration")), distance_meters=_number(line.get("distance"))))
                    parts.append(line)
        for part in parts:
            if not transit:
                steps.append(RouteStep(mode=query.mode, instruction=_text(part.get("instruction")),
                    duration_seconds=_number((part.get("cost") or {}).get("duration")), distance_meters=_number(part.get("step_distance"))))
            polyline = _polyline_text(part.get("polyline"))
            if not polyline:
                geometry_complete = False
            for pair in (polyline or "").split(";"):
                if pair:
                    point = _coordinates(pair)
                    if not points or point != points[-1]:
                        points.append(point)
        warnings = [] if transit else ["departure_time_not_supported_by_this_endpoint; estimate_is_not_a_future_traffic_forecast"]
        if not points or not geometry_complete:
            warnings.append("provider_missing_or_incomplete_geometry")
        return route_result(query, "amap", status="ok", duration_seconds=duration, distance_meters=distance,
            departure_time_applied=transit, steps=steps, warnings=warnings,
            geometry=RouteGeometry(crs="GCJ02", encoding="points", points=points) if points and geometry_complete else None)
