"""Visits and provider route estimates; delivery checks remain a runtime duty."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.common import ContractModel, Identifier, Money, ProviderName, TravelMode, utc_now
from app.schemas.place import Coordinates

RouteStatus = Literal["ok", "no_route", "unsupported", "permission_denied", "rate_limited", "unavailable", "unknown"]


class RouteGeometry(ContractModel):
    crs: Literal["GCJ02", "WGS84", "BD09"]
    encoding: Literal["points", "google_polyline"]
    points: list[Coordinates] = Field(default_factory=list)
    encoded_polyline: str | None = None

    @model_validator(mode="after")
    def geometry_consistent(self):
        if self.encoding == "points":
            if not self.points or self.encoded_polyline:
                raise ValueError("points geometry requires points only")
            if any(point.crs != self.crs for point in self.points):
                raise ValueError("geometry coordinate systems differ")
        elif not self.encoded_polyline or self.points:
            raise ValueError("encoded geometry requires polyline only")
        return self


class RouteStep(ContractModel):
    mode: TravelMode
    instruction: str | None = None
    line_name: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_meters: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class RouteLeg(ContractModel):
    leg_id: Identifier
    from_place_id: Identifier
    to_place_id: Identifier
    mode: TravelMode
    provider: ProviderName
    status: RouteStatus
    requested_departure_at: AwareDatetime
    queried_at: AwareDatetime = Field(default_factory=utc_now)
    departure_time_applied: bool = False
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    distance_meters: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    geometry: RouteGeometry | None = None
    steps: list[RouteStep] = Field(default_factory=list)
    evidence_ref: Identifier
    unknown_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def result_consistent(self):
        if self.status == "ok":
            if self.duration_seconds is None or self.distance_meters is None or self.unknown_reason:
                raise ValueError("ok route requires duration and distance, without unknown_reason")
        else:
            if not self.unknown_reason:
                raise ValueError("non-ok route needs a reason")
            if self.duration_seconds is not None or self.distance_meters is not None or self.geometry or self.steps:
                raise ValueError("non-ok route must not carry usable estimates")
        return self


class Visit(ContractModel):
    visit_id: Identifier
    kind: Literal["place", "activity"] = "place"
    place_id: Identifier | None = None
    activity_label: Identifier | None = None
    date: date
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    dwell_seconds: int | None = Field(default=None, ge=0)
    locked: bool = False
    evidence_refs: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if self.kind == "place" and (not self.place_id or self.activity_label):
            raise ValueError("place visit needs place_id only")
        if self.kind == "activity" and (self.place_id or not self.activity_label):
            raise ValueError("activity needs activity_label only")
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("visit times must be both present or both absent")
        if self.start_at and self.end_at:
            if self.end_at <= self.start_at or self.start_at.date() != self.date:
                raise ValueError("visit time range is inconsistent with its date")
        return self


class Stay(ContractModel):
    stay_id: Identifier
    hotel_place_id: Identifier
    check_in: date
    check_out: date
    party_count: int | None = Field(default=None, ge=1)
    rooms: int | None = Field(default=None, ge=1)
    quote_ref: Identifier | None = None
    locked: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.check_out <= self.check_in:
            raise ValueError("stay check_out must follow check_in")
        return self


class DayPlan(ContractModel):
    date: date
    visits: list[Visit] = Field(default_factory=list)
    routes: list[RouteLeg] = Field(default_factory=list)

    @model_validator(mode="after")
    def dates_match(self):
        if any(visit.date != self.date for visit in self.visits):
            raise ValueError("visit date must match day")
        return self


class ValidationReport(ContractModel):
    status: Literal["passed", "failed", "unknown"] = "unknown"
    issues: list[str] = Field(default_factory=list)
    evidence_refs: list[Identifier] = Field(default_factory=list)


class PlanRef(ContractModel):
    plan_id: Identifier
    version: int = Field(ge=1)


class PlanVersion(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: Identifier
    version: int = Field(ge=1)
    base_state_version: int = Field(ge=0)
    intent_snapshot_ref: Identifier
    status: Literal["draft", "partial", "verified"] = "draft"
    days: list[DayPlan] = Field(default_factory=list)
    stays: list[Stay] = Field(default_factory=list)
    costs: list[Money] = Field(default_factory=list)
    cost_coverage: list[str] = Field(default_factory=list)
    validation_report: ValidationReport = Field(default_factory=ValidationReport)

    @model_validator(mode="after")
    def no_false_completion(self):
        ids = [visit.visit_id for day in self.days for visit in day.visits]
        if len(set(ids)) != len(ids) or len({day.date for day in self.days}) != len(self.days):
            raise ValueError("duplicate visit IDs or day dates")
        if self.status == "verified":
            if not self.days or self.validation_report.status != "passed" or self.validation_report.issues:
                raise ValueError("verified plan requires nonempty days and a passed report without issues")
            if any(leg.status != "ok" for day in self.days for leg in day.routes):
                raise ValueError("verified plan cannot contain unverified routes")
            for day in self.days:
                places = [v.place_id for v in day.visits if v.place_id]
                available = {(leg.from_place_id, leg.to_place_id) for leg in day.routes}
                if any(a != b and (a, b) not in available for a, b in zip(places, places[1:])):
                    raise ValueError("verified plan cannot omit required routes")
        return self
