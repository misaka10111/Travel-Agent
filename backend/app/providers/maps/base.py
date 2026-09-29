"""Small synchronous provider protocol for explicit P0 probes."""

from typing import Literal, Protocol
from functools import wraps
from uuid import uuid4

from pydantic import AwareDatetime, Field, ValidationError

from app.schemas.common import ContractModel, ProviderName, RegionRef, TravelMode
from app.schemas.place import Place
from app.schemas.plan import RouteLeg

ErrorCode = Literal["missing_credentials", "permission_denied", "rate_limited", "budget_exhausted", "unsupported", "invalid_request", "invalid_response", "unavailable"]


class MapError(Exception):
    """Messages are fixed by adapters; never include request URLs or raw bodies."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False, provider_code: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider_code = provider_code

    def summary(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable, "provider_code": self.provider_code}


def normalize_response_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValidationError, TypeError, AttributeError, KeyError):
            raise MapError("invalid_response", "Map provider returned malformed fields.") from None
    return wrapped


class ProviderCapabilities(ContractModel):
    provider: ProviderName
    country_scope: list[str]
    modes: list[TravelMode]
    text_search: bool
    place_details: bool
    nearby_search: bool = False
    account_verified: bool = False


class RouteQuery(ContractModel):
    origin: Place
    destination: Place
    mode: TravelMode
    departure_at: AwareDatetime


class MapProvider(Protocol):
    capabilities: ProviderCapabilities

    def search_places(self, query: str, region: RegionRef, limit: int = 5) -> list[Place]: ...
    def get_place(self, provider_place_id: str, region: RegionRef) -> Place: ...
    def route(self, query: RouteQuery) -> RouteLeg: ...


def route_result(query: RouteQuery, provider: ProviderName, **result) -> RouteLeg:
    return RouteLeg(
        leg_id=str(uuid4()), from_place_id=query.origin.place_id,
        to_place_id=query.destination.place_id, mode=query.mode, provider=provider,
        requested_departure_at=query.departure_at, evidence_ref=f"{provider}:request:{uuid4()}",
        **result,
    )


def validate_endpoints(query: RouteQuery, crs: str, country: str | None = None) -> None:
    for place in (query.origin, query.destination):
        if place.coordinates is None or place.coordinates.crs != crs:
            raise MapError("invalid_request", "Route endpoints require the provider's original coordinate system.")
        if country is not None and place.region.country_code != country:
            raise MapError("unsupported", "This adapter has no verified configuration for the requested country.")
