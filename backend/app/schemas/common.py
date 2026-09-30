"""Shared units, provenance and strict boundaries for the new planning contracts."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TravelMode = Literal["walking", "transit", "driving"]
ProviderName = Literal["amap", "google"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def check_timezone(value: str | None) -> str | None:
    if value is not None:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an available IANA timezone") from exc
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class FieldEvidence(ContractModel):
    source: Literal["profile", "form", "message", "answer", "selection", "provider", "inferred"]
    source_ref: Identifier
    confirmation: Literal["explicit", "inferred", "unknown"]
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class Money(ContractModel):
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    scope: Literal["trip_total", "per_person", "per_person_per_day", "per_night", "per_room_night", "ticket", "route"]


class RegionRef(ContractModel):
    label: Identifier
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region_id: Identifier | None = None
    timezone: str | None = None
    identity_status: Literal["unresolved", "candidate", "confirmed"] = "unresolved"

    _timezone = field_validator("timezone")(check_timezone)


class TimeWindow(ContractModel):
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("end")
    @classmethod
    def ordered(cls, value, info):
        if "start" in info.data and value <= info.data["start"]:
            raise ValueError("end must follow start")
        return value
