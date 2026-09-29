"""Place identity and provider facts without silently guessing coordinates."""

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from app.schemas.common import ContractModel, Identifier, ProviderName, RegionRef, utc_now


class Coordinates(ContractModel):
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    crs: Literal["GCJ02", "WGS84", "BD09"]
    conversion_source_ref: Identifier | None = None


class ProviderRef(ContractModel):
    provider: ProviderName
    provider_place_id: Identifier


class Fact(ContractModel):
    value: JsonValue = None
    source_ref: Identifier
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def missing_is_explicit(self):
        if self.value is None and not self.unknown_reason:
            raise ValueError("unknown fact requires a reason")
        if self.value is not None and self.unknown_reason:
            raise ValueError("known fact cannot also be unknown")
        if self.valid_until and self.valid_until < self.observed_at:
            raise ValueError("valid_until precedes observed_at")
        return self


class PlaceRelation(ContractModel):
    kind: Literal["inside", "nearby", "same_identity"]
    place_id: Identifier
    evidence_ref: Identifier


class Place(ContractModel):
    place_id: Identifier
    name: Identifier
    category: Literal["attraction", "restaurant", "hotel", "area", "transport", "other"]
    region: RegionRef
    provider_refs: list[ProviderRef] = Field(default_factory=list)
    coordinates: Coordinates | None = None
    coordinate_unknown_reason: str | None = None
    identity_status: Literal["unresolved", "candidate", "confirmed"] = "unresolved"
    address: str | None = None
    aliases: list[str] = Field(default_factory=list)
    relations: list[PlaceRelation] = Field(default_factory=list)
    facts: dict[str, Fact] = Field(default_factory=dict)
    observed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def coordinate_status(self):
        if self.coordinates is None and not self.coordinate_unknown_reason:
            raise ValueError("missing coordinates require a reason")
        if self.coordinates is not None and self.coordinate_unknown_reason:
            raise ValueError("known coordinates cannot also be unknown")
        return self
