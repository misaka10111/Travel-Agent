"""Durable state contracts; P1 will implement transitions and persistence."""

from datetime import date as Date
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel, FieldEvidence, Identifier, TimeWindow
from app.schemas.intent import TripIntent
from app.schemas.place import Place
from app.schemas.plan import PlanRef


class QuestionOption(ContractModel):
    option_id: Identifier
    label: Identifier


class Question(ContractModel):
    question_id: Identifier
    state_version: int = Field(ge=0)
    gap_ids: list[Identifier] = Field(min_length=1)
    prompt: Identifier
    mode: Literal["text", "single", "multiple"] = "text"
    options: list[QuestionOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def choices(self):
        ids = [option.option_id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate option IDs")
        if self.mode == "text" and self.options:
            raise ValueError("text question must not have options")
        if self.mode != "text" and len(self.options) < 2:
            raise ValueError("choice question requires at least two options")
        return self


class Answer(ContractModel):
    request_id: Identifier
    question_id: Identifier
    state_version: int = Field(ge=0)
    text: Identifier | None = None
    option_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def meaningful(self):
        if not self.text and not self.option_ids:
            raise ValueError("answer needs text or option IDs")
        if len(set(self.option_ids)) != len(self.option_ids):
            raise ValueError("duplicate answer options")
        return self

    def check_question(self, question: Question) -> None:
        if self.question_id != question.question_id or self.state_version != question.state_version:
            raise ValueError("answer refers to a different question or stale state")
        allowed = {option.option_id for option in question.options}
        if not set(self.option_ids) <= allowed:
            raise ValueError("unknown option ID")
        if question.mode == "single" and len(self.option_ids) > 1:
            raise ValueError("single question accepts at most one option")


class Selection(ContractModel):
    selection_id: Identifier
    place_id: Identifier
    decision: Literal["include", "exclude", "lock"]
    date: Date | None = None
    time_window: TimeWindow | None = None
    evidence: FieldEvidence


class CallBudget(ContractModel):
    limit: int = Field(default=20, ge=1)
    used: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def within_limit(self):
        if self.used > self.limit:
            raise ValueError("calls used exceeds limit")
        return self


class SessionState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: Identifier
    owner_ref: Identifier
    trip_ref: Identifier | None = None
    state_version: int = Field(ge=0)
    intent_snapshot: TripIntent
    candidates: list[Place] = Field(default_factory=list)
    selections: list[Selection] = Field(default_factory=list)
    current_plan_ref: PlanRef | None = None
    pending_questions: list[Question] = Field(default_factory=list)
    budget_usage: CallBudget = Field(default_factory=CallBudget)
    status: Literal["draft", "running", "waiting_user", "needs_attention", "completed", "cancelled", "failed"] = "draft"

    @model_validator(mode="after")
    def consistent_state(self):
        for values, attr in ((self.candidates, "place_id"), (self.selections, "selection_id"), (self.pending_questions, "question_id")):
            ids = [getattr(value, attr) for value in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {attr}")
        if any(question.state_version != self.state_version for question in self.pending_questions):
            raise ValueError("pending question references stale state")
        if self.status == "waiting_user" and not self.pending_questions:
            raise ValueError("waiting_user requires pending questions")
        if self.status == "completed" and (self.current_plan_ref is None or self.pending_questions):
            raise ValueError("completed session needs a plan and no pending questions")
        return self
