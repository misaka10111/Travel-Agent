"""Minimal Places New / Routes adapter; no live account has been validated."""

from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from pydantic import SecretStr, ValidationError

from app.providers.maps.base import MapError, ProviderCapabilities, RouteQuery, route_result, validate_endpoints
from app.providers.maps.transport import MapTransport
from app.schemas.common import RegionRef
from app.schemas.place import Coordinates, Place, ProviderRef
from app.schemas.plan import RouteGeometry

PLACE_FIELDS = "id,displayName,location,formattedAddress,primaryType"


class GoogleProvider:
    capabilities = ProviderCapabilities(provider="google", country_scope=["city_coverage_requires_verification"],
        modes=["walking", "transit", "driving"], text_search=True, place_details=True)

    def __init__(self, key: SecretStr | None, transport: MapTransport):
        self._key = key
        self.transport = transport

    def _headers(self, fields: str):
        if not self._key or not self._key.get_secret_value():
            raise MapError("missing_credentials", "GOOGLE_MAPS_API_KEY is not configured.")
        return {"X-Goog-Api-Key": self._key.get_secret_value(), "X-Goog-FieldMask": fields}

    @staticmethod
    def _place(item: dict, region: RegionRef) -> Place:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise MapError("invalid_response", "Google POI is missing its ID.")
        try:
            name = (item.get("displayName") or {}).get("text")
            location = item.get("location")
            coordinates = Coordinates(crs="WGS84", **location) if location is not None else None
            primary_type = item.get("primaryType") or ""
            category = {"lodging": "hotel", "hotel": "hotel", "restaurant": "restaurant",
                        "tourist_attraction": "attraction", "museum": "attraction", "park": "attraction"}.get(primary_type, "other")
            return Place(place_id=str(uuid5(NAMESPACE_URL, "google:" + item["id"])), name=name, category=category,
                region=region, provider_refs=[ProviderRef(provider="google", provider_place_id=item["id"])],
                coordinates=coordinates, coordinate_unknown_reason=None if coordinates else "provider_missing_coordinates",
                identity_status="candidate", address=item.get("formattedAddress"))
        except (ValidationError, TypeError, AttributeError):
            raise MapError("invalid_response", "Google returned malformed POI fields.") from None

    def search_places(self, query: str, region: RegionRef, limit: int = 5) -> list[Place]:
        if not query.strip() or not 1 <= limit <= 20:
            raise MapError("invalid_request", "Google search requires a query and limit between 1 and 20.")
        body = {"textQuery": f"{query}, {region.label}", "pageSize": limit}
        if region.country_code:
            body["regionCode"] = region.country_code
        data = self.transport.request("POST", "https://places.googleapis.com/v1/places:searchText",
            json=body, headers=self._headers(",".join("places." + field for field in PLACE_FIELDS.split(","))))
        places = data.get("places", [])
        if not isinstance(places, list):
            raise MapError("invalid_response", "Google search response contains an invalid POI list.")
        return [self._place(item, region) for item in places]

    def get_place(self, provider_place_id: str, region: RegionRef) -> Place:
        data = self.transport.request("GET", "https://places.googleapis.com/v1/places/" + quote(provider_place_id, safe=""),
            headers=self._headers(PLACE_FIELDS))
        if data.get("id") != provider_place_id:
            raise MapError("invalid_response", "Google detail returned a different POI ID.")
        return self._place(data, region)

    def route(self, query: RouteQuery):
        validate_endpoints(query, "WGS84")
        body = {"origin": {"location": {"latLng": {"latitude": query.origin.coordinates.latitude, "longitude": query.origin.coordinates.longitude}}},
            "destination": {"location": {"latLng": {"latitude": query.destination.coordinates.latitude, "longitude": query.destination.coordinates.longitude}}},
            "travelMode": {"walking": "WALK", "transit": "TRANSIT", "driving": "DRIVE"}[query.mode]}
        # WALK doesn't support a future departure forecast; don't silently imply it did.
        if query.mode in ("transit", "driving"):
            body["departureTime"] = query.departure_at.isoformat()
        if query.mode == "driving":
            body["routingPreference"] = "TRAFFIC_AWARE"
        data = self.transport.request("POST", "https://routes.googleapis.com/directions/v2:computeRoutes",
            json=body, headers=self._headers("routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"))
        routes = data.get("routes", [])
        if not isinstance(routes, list):
            raise MapError("invalid_response", "Google route response contains invalid options.")
        if not routes:
            return route_result(query, "google", status="no_route", unknown_reason="provider_returned_no_route")
        try:
            route = routes[0]
            duration_text = route.get("duration")
            distance = route.get("distanceMeters")
            if duration_text is None or distance is None:
                return route_result(query, "google", status="unknown", unknown_reason="provider_missing_duration_or_distance")
            if not isinstance(duration_text, str) or not duration_text.endswith("s") or isinstance(distance, bool):
                raise ValueError("invalid units")
            duration = float(duration_text[:-1])
            encoded = (route.get("polyline") or {}).get("encodedPolyline")
            warnings = [] if query.mode != "walking" else ["departure_time_not_applied_to_walking_estimate"]
            if not encoded:
                warnings.append("provider_missing_geometry")
            return route_result(query, "google", status="ok", duration_seconds=duration, distance_meters=distance,
                departure_time_applied=query.mode != "walking", warnings=warnings,
                geometry=RouteGeometry(crs="WGS84", encoding="google_polyline", encoded_polyline=encoded) if encoded else None)
        except (ValueError, TypeError, AttributeError):
            raise MapError("invalid_response", "Google returned malformed route units or fields.") from None
