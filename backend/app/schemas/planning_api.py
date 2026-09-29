"""P1 requests carry a version and command ID; tokens are returned only at creation."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel, Identifier, RegionRef
from app.schemas.intent import Budget, Constraint, Party, Preferences, TripDates, TripIntent
from app.schemas.session import Answer, Selection, SessionState


class PreferencePatch(ContractModel):
    interests: list[str] | None = None
    comfort_tags: list[str] | None = None
    travel_modes: list[Literal["walking", "transit", "driving"]] | None = None
    pace: Literal["relaxed", "balanced", "busy"] | None = None
    dietary_preferences: list[str] | None = None
    lodging_preferences: list[str] | None = None
    must_visit_names: list[str] | None = None


class ProfileSnapshot(ContractModel):
    age_group: str | None = Field(default=None, max_length=100)
    gender: str | None = Field(default=None, max_length=100)
    identity: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    travel_style: list[str] = Field(default_factory=list, max_length=20)
    preferences: Preferences = Field(default_factory=Preferences)


class IntentPatch(ContractModel):
    destination: RegionRef | None = None
    origin: RegionRef | None = None
    dates: TripDates | None = None
    party: Party | None = None
    budget: Budget | None = None
    preferences: PreferencePatch | None = None
    constraints: list[Constraint] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class CreateSession(ContractModel):
    intent: TripIntent | None = None
    profile: ProfileSnapshot = Field(default_factory=ProfileSnapshot)
    message: str = Field(default="", max_length=4000)
    task_scope: Literal["candidates", "itinerary"] = "candidates"

    @model_validator(mode="after")
    def input_present(self):
        if self.intent is None and not self.message.strip():
            raise ValueError("intent or a nonempty message is required")
        return self


class VersionCommand(ContractModel):
    request_id: Identifier
    base_state_version: int = Field(ge=0)


class EditSession(VersionCommand):
    intent_patch: IntentPatch | None = None
    selections: list[Selection] | None = Field(default=None, max_length=50)
    message: str | None = Field(default=None, min_length=1, max_length=4000)


class AnswerSession(ContractModel):
    answer: Answer
    # Optional structured form answer avoids an unnecessary model interpretation call.
    intent_patch: IntentPatch | None = None


class SessionView(ContractModel):
    state: SessionState
    profile: ProfileSnapshot
    usage: dict[str, int]
    available_actions: list[str]
    replayed: bool = False


class CreatedSession(SessionView):
    access_token: str


class SessionEvent(ContractModel):
    event_id: int
    state_version: int
    kind: str
    data: dict
