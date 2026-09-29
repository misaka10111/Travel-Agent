"""Trip-specific intent; draft requirements may remain unknown."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator, field_validator

from app.schemas.common import ContractModel, FieldEvidence, Identifier, Money, RegionRef, TimeWindow, TravelMode, check_timezone


class TripDates(ContractModel):
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1)
    timezone: str | None = None

    _timezone = field_validator("timezone")(check_timezone)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_date and not self.start_date:
            raise ValueError("end_date requires start_date")
        if self.start_date and self.end_date:
            days = (self.end_date - self.start_date).days + 1
            if days < 1 or (self.duration_days is not None and days != self.duration_days):
                raise ValueError("dates and inclusive duration_days conflict")
        return self


class Party(ContractModel):
    count: int | None = Field(default=None, ge=1)
    count_min: int | None = Field(default=None, ge=1)
    count_status: Literal["exact", "lower_bound", "unknown"] = "unknown"
    raw: str | None = None
    companion_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.count_status == "exact" and (self.count is None or self.count_min is not None):
            raise ValueError("exact party needs count only")
        if self.count_status == "lower_bound" and (self.count_min is None or self.count is not None):
            raise ValueError("lower_bound party needs count_min only")
        if self.count_status == "unknown" and (self.count is not None or self.count_min is not None):
            raise ValueError("unknown party cannot carry a confirmed count")
        return self


class Budget(ContractModel):
    money: Money | None = None
    limit_kind: Literal["hard", "target", "unspecified"] = "unspecified"
    raw: str | None = None
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def known_limit(self):
        if self.limit_kind in ("hard", "target") and self.money is None:
            raise ValueError("numeric budget limit requires amount, currency and scope")
        return self


class Preferences(ContractModel):
    interests: list[str] = Field(default_factory=list)
    comfort_tags: list[str] = Field(default_factory=list)
    travel_modes: list[TravelMode] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "busy"] | None = None


class ConstraintBase(ContractModel):
    constraint_id: Identifier
    strength: Literal["hard", "soft"]
    evidence: FieldEvidence


class PlaceConstraint(ConstraintBase):
    kind: Literal["must_visit", "exclude"]
    place_id: Identifier


class WalkingConstraint(ConstraintBase):
    kind: Literal["walking_limit"]
    max_daily_meters: int = Field(gt=0)


class ReservationConstraint(ConstraintBase):
    kind: Literal["reservation"]
    place_id: Identifier
    time_window: TimeWindow


class HotelConstraint(ConstraintBase):
    kind: Literal["booked_hotel"]
    place_id: Identifier
    check_in: date
    check_out: date

    @model_validator(mode="after")
    def dates_ordered(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must follow check_in")
        return self


class SpecialRequirement(ConstraintBase):
    kind: Literal["special_requirement"]
    description: Identifier


Constraint = Annotated[
    PlaceConstraint | WalkingConstraint | ReservationConstraint | HotelConstraint | SpecialRequirement,
    Field(discriminator="kind"),
]


class TripIntent(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    intent_id: Identifier
    profile_snapshot_ref: Identifier | None = None
    destination: RegionRef
    origin: RegionRef | None = None
    dates: TripDates = Field(default_factory=TripDates)
    party: Party = Field(default_factory=Party)
    budget: Budget = Field(default_factory=Budget)
    preferences: Preferences = Field(default_factory=Preferences)
    constraints: list[Constraint] = Field(default_factory=list)
    field_evidence: dict[str, FieldEvidence] = Field(default_factory=dict)
    unresolved_questions: list[Identifier] = Field(default_factory=list)
    message_refs: list[Identifier] = Field(default_factory=list)
    notes: str = ""
