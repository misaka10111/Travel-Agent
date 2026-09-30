"""Observed offers are not bookings; missing currency/scope remain explicit."""

from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from app.schemas.common import ContractModel


class TravelSegment(ContractModel):
    service_no: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    origin_station: str | None = None
    destination_station: str | None = None
    departure_at: AwareDatetime | None = None
    arrival_at: AwareDatetime | None = None
    seat: str | None = None


class TravelOffer(ContractModel):
    offer_id: str = Field(default_factory=lambda: str(uuid4()))
    category: Literal["hotel", "train", "flight"]
    direction: Literal["stay", "outbound", "inbound"]
    name: str | None = None
    supplier_offer_id: str | None = None
    address: str | None = None
    segments: list[TravelSegment] = Field(default_factory=list)
    price_display: str | None = None
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    price_scope: Literal["unknown", "ticket", "per_room_night", "stay_total"] = "unknown"
    observed_at: AwareDatetime
    query_start_date: date | None = None
    query_end_date: date | None = None
    url: str | None = None
    identity_match: Literal["unresolved", "matched"] = "unresolved"
    map_place_id: str | None = None
    origin_place_id: str | None = None
    destination_place_id: str | None = None
    availability: Literal["supplier_observed_not_booked"] = "supplier_observed_not_booked"
