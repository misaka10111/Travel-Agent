"""Whitelisted, discriminated actions and typed observations for Plan Agent."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from app.schemas.common import ContractModel, Identifier, Money, RegionRef, TimeWindow
from app.schemas.place import Place, ProviderRef
from app.schemas.plan import PlanRef, PlanVersion, ValidationReport
from app.schemas.session import Question


class ActionScope(ContractModel):
    dates: list[date] = Field(default_factory=list)
    place_ids: list[Identifier] = Field(default_factory=list)


class ActionBase(ContractModel):
    action_id: Identifier
    base_state_version: int = Field(ge=0)
    scope: ActionScope = Field(default_factory=ActionScope)
    purpose: Identifier


class AskUserAction(ActionBase):
    kind: Literal["ask_user"]
    gap_ids: list[Literal["destination", "origin", "dates", "party", "budget", "preferences", "constraints", "notes"]] = Field(min_length=1, max_length=3)
    question_goal: Identifier


class SearchFilters(ContractModel):
    keywords: list[Identifier] = Field(default_factory=list)
    exclude_place_ids: list[Identifier] = Field(default_factory=list)
    max_price: Money | None = None
    accessible: bool | None = None


class SearchCandidatesAction(ActionBase):
    kind: Literal["search_candidates"]
    intent_ref: Identifier
    categories: list[Literal["attraction", "restaurant", "hotel", "transport"]] = Field(min_length=1)
    region: RegionRef
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=10, ge=1, le=50)


class PlaceDetailsAction(ActionBase):
    kind: Literal["get_place_details"]
    place_id: Identifier
    provider_ref: ProviderRef
    fields: list[Literal["coordinates", "address", "opening_hours", "rating", "price"]] = Field(min_length=1)


class ComputeItineraryAction(ActionBase):
    kind: Literal["compute_itinerary"]
    intent_ref: Identifier
    candidate_ids: list[Identifier] = Field(min_length=1)
    selection_ref: Identifier


class RankNearbyAction(ActionBase):
    kind: Literal["rank_nearby"]
    anchor_refs: list[Identifier] = Field(min_length=1)
    category: Literal["restaurant", "hotel"]
    time_window: TimeWindow
    preference_ref: Identifier


class AddVisitChange(ContractModel):
    kind: Literal["add_visit"]
    place_id: Identifier
    date: date


class RemoveVisitChange(ContractModel):
    kind: Literal["remove_visit"]
    visit_id: Identifier


class MoveVisitChange(ContractModel):
    kind: Literal["move_visit"]
    visit_id: Identifier
    date: date


class ReplaceHotelChange(ContractModel):
    kind: Literal["replace_hotel"]
    stay_id: Identifier
    hotel_place_id: Identifier


PlanChange = Annotated[AddVisitChange | RemoveVisitChange | MoveVisitChange | ReplaceHotelChange, Field(discriminator="kind")]


class EditPlanAction(ActionBase):
    kind: Literal["edit_plan"]
    plan_ref: PlanRef
    changes: list[PlanChange] = Field(min_length=1)


class ValidatePlanAction(ActionBase):
    kind: Literal["validate_plan"]
    plan_ref: PlanRef
    checks: list[Literal["duplicates", "time_windows", "locks", "budget", "evidence", "experience"]] = Field(min_length=1)


class FinishAction(ActionBase):
    kind: Literal["finish"]
    plan_ref: PlanRef


class PauseAction(ActionBase):
    kind: Literal["pause"]
    reason: Identifier


AgentAction = Annotated[
    AskUserAction | SearchCandidatesAction | PlaceDetailsAction | ComputeItineraryAction |
    RankNearbyAction | EditPlanAction | ValidatePlanAction | FinishAction | PauseAction,
    Field(discriminator="kind"),
]
AGENT_ACTION_ADAPTER = TypeAdapter(AgentAction)


class QuestionsPayload(ContractModel):
    kind: Literal["questions"]
    questions: list[Question] = Field(min_length=1)


class PlacesPayload(ContractModel):
    kind: Literal["places"]
    places: list[Place]


class PlanPayload(ContractModel):
    kind: Literal["plan"]
    plan: PlanVersion


class AuditPayload(ContractModel):
    kind: Literal["audit"]
    report: ValidationReport


class RankingItem(ContractModel):
    place_id: Identifier
    score: float | None = Field(default=None, allow_inf_nan=False)
    detour_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    explanation: str


class RankingPayload(ContractModel):
    kind: Literal["ranking"]
    items: list[RankingItem]


ToolPayload = Annotated[QuestionsPayload | PlacesPayload | PlanPayload | AuditPayload | RankingPayload, Field(discriminator="kind")]


class ToolError(ContractModel):
    code: Identifier
    message: Identifier
    retryable: bool = False


class ToolResult(ContractModel):
    action_id: Identifier
    base_state_version: int = Field(ge=0)
    status: Literal["success", "partial", "error"]
    payload: ToolPayload | None = None
    error: ToolError | None = None
    evidence_refs: list[Identifier] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    calls_used: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent_result(self):
        if self.status == "success" and (self.payload is None or self.error is not None):
            raise ValueError("success requires payload without error")
        if self.status == "error" and (self.error is None or self.payload is not None):
            raise ValueError("error requires error without payload")
        if self.status == "partial" and (self.payload is None or not (self.error or self.warnings)):
            raise ValueError("partial requires payload and an explanation")
        return self
