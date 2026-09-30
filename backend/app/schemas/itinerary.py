"""Relative-day drafts are distinct from dated, evidence-verified delivery."""

from datetime import date as Date
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel, Identifier
from app.schemas.plan import RouteLeg


class Stop(ContractModel):
    stop_id: Identifier
    place_id: Identifier
    category: Literal["attraction", "restaurant", "hotel", "transport"]
    start_minute: int | None = Field(default=None, ge=0, le=1439)
    end_minute: int | None = Field(default=None, ge=0, le=1440)
    dwell_minutes: int = Field(ge=1)
    locked: bool = False
    child_place_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def times(self):
        if (self.start_minute is None) != (self.end_minute is None):
            raise ValueError("both times must be known or unknown")
        if self.start_minute is not None and self.end_minute - self.start_minute < self.dwell_minutes:
            raise ValueError("stop must contain its dwell duration")
        return self


class DraftDay(ContractModel):
    day_index: int = Field(ge=1)
    date: Date | None = None
    stops: list[Stop] = Field(default_factory=list)
    routes: list[RouteLeg] = Field(default_factory=list)
    hotel_place_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class PlanningIssue(ContractModel):
    code: str
    severity: Literal["blocking", "unknown", "warning"]
    message: str
    day_index: int | None = None
    place_ids: list[str] = Field(default_factory=list)


class DraftAudit(ContractModel):
    plan_version: int = Field(ge=1)
    hard_status: Literal["passed", "failed", "unknown"] = "unknown"
    experience_status: Literal["passed", "failed", "unknown"] = "unknown"
    issues: list[PlanningIssue] = Field(default_factory=list)
    reviewed: bool = False


class Recommendation(ContractModel):
    place_id: str
    category: Literal["restaurant", "hotel"]
    score_components: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class ItineraryDraft(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    plan_id: Identifier
    version: int = Field(ge=1)
    base_state_version: int = Field(ge=0)
    intent_snapshot_ref: Identifier
    status: Literal["draft", "partial", "verified"] = "draft"
    days: list[DraftDay]
    recommendations: list[Recommendation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    supplier_status: dict[str, str] = Field(default_factory=dict)
    travel_offers: list[dict] = Field(default_factory=list)
    audit: DraftAudit | None = None

    @model_validator(mode="after")
    def no_false_verification(self):
        if [d.day_index for d in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError("days must be contiguous relative indices")
        if self.status == "verified":
            if not self.days or any(d.date is None or not d.stops for d in self.days):
                raise ValueError("verified plans require dated nonempty days")
            if not self.audit or not self.audit.reviewed or self.audit.plan_version != self.version or self.audit.issues:
                raise ValueError("verified plans require a current complete audit")
            if self.audit.hard_status != "passed" or self.audit.experience_status != "passed" or self.missing_requirements:
                raise ValueError("unknown evidence cannot be verified")
            for day in self.days:
                required = list(zip([s.place_id for s in day.stops], [s.place_id for s in day.stops][1:]))
                available = {(r.from_place_id, r.to_place_id) for r in day.routes if r.status == "ok"}
                if any(a != b and (a, b) not in available for a, b in required):
                    raise ValueError("verified plan has missing required routes")
        return self


class PlanManifest(ContractModel):
    """Own arrangement only: no supplier names, coordinates, facts or route payloads."""
    plan_id: str
    version: int
    assignments: list[list[str]]
    change_reason: str
