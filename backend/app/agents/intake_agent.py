"""Typed natural-language intake; only user-provided facts become requirements."""

from pydantic import Field

from app.schemas.common import ContractModel
from app.schemas.planning_api import IntentPatch


class IntakeResult(ContractModel):
    patch: IntentPatch = Field(default_factory=IntentPatch)
    unknown_fields: list[str] = Field(default_factory=list)
    declined_fields: list[str] = Field(default_factory=list)


class IntakeAgent:
    def __init__(self, client):
        self.client = client

    async def interpret(self, message, intent, repair=None):
        result = await self.client.complete(
            "Extract ONLY explicit trip information from this Chinese user message into patch. Return JSON. "
            "A patch contains ONLY changed fields. Preserve uncertain dates/budget as unknown, NEVER invent them. "
            "For dates/party/budget return a coherent whole unit, using known values when appropriate. "
            "For preferences return only updated subfields; retain existing list items unless user removes them. "
            "Use dietary_preferences for 不辣/清淡/健康, lodging_preferences for 景点附近/交通便利, "
            "interests for 人文/古代建筑, comfort_tags for 舒适, pace=relaxed only if expressed. "
            "Dislike spicy is not allergy; two people does not specify rooms. "
            "Resolve unambiguous mainland cities as country_code=CN, timezone=Asia/Shanghai, identity_status=candidate. "
            "Do not infer country for ambiguous destinations. Do not infer preferences from age/gender. "
            "If user says 暂不确定 put dates/budget into unknown_fields only when applicable; "
            "explicit refusal goes into declined_fields. Clear fields only at explicit user request. "
            "For unknown dates preserve explicitly stated duration_days. No arbitrary place IDs or fake reservations.",
            {"message": message, "current_intent": intent.model_dump(mode="json"), "repair": repair},
            IntakeResult.model_json_schema())
        return IntakeResult.model_validate(result)
