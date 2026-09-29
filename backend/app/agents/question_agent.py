from typing import Literal

from pydantic import Field

from app.schemas.common import ContractModel, Identifier
from app.schemas.planning_api import IntentPatch
from app.schemas.session import Question, QuestionOption


class QuestionDraft(ContractModel):
    prompt: Identifier
    mode: Literal["text", "single", "multiple"] = "text"
    options: list[QuestionOption] = Field(default_factory=list, max_length=6)


class QuestionAgent:
    def __init__(self, client):
        self.client = client

    async def ask(self, action, snapshot, question_id):
        result = await self.client.complete(
            "You are QAgent. Ask ONE concise Chinese question to resolve the requested gaps. "
            'Prefer text mode. Example JSON: {"prompt":"这次共有几位旅行者？","mode":"text","options":[]}. '
            "Use existing information; never ask answered facts again. A choice needs at least 2 options with stable IDs. "
            "Use text for dates or ambiguous money. Do not invent assumptions. Return JSON only.",
            {"gap_ids": action.gap_ids, "goal": action.question_goal,
             "intent": snapshot.state.intent_snapshot.model_dump(mode="json"), "messages": snapshot.messages[-12:]},
            QuestionDraft.model_json_schema())
        draft = QuestionDraft.model_validate(result)
        return Question(question_id=question_id, state_version=snapshot.state.state_version,
            gap_ids=action.gap_ids, **draft.model_dump())

    async def interpret(self, queued, intent):
        patch = await self.client.complete(
            "Interpret ONLY this user answer into a trip intent patch. Return JSON. Only update allowed gap_ids. "
            "Fields are whole validated units; use existing values for unchanged subfields. "
            "Unknown/ambiguous facts must remain unknown. Do not guess exact count, dates, money currency or budget scope. "
            "Do not infer age-based or gender-based preferences. If not enough evidence return {} and ask again later.",
            {"question": queued["question"], "answer": queued["answer"], "intent": intent.model_dump(mode="json")},
            IntentPatch.model_json_schema())
        return IntentPatch.model_validate(patch)
