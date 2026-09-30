"""Typed natural-language intake; only user-provided facts become requirements."""

import re

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
            "Use morning_style=early for 喜欢早起, breakfast_required=true for 要吃早饭, "
            "day_start_time/day_end_time only for user-given clock times, compact_nearby=true for 邻近景点尽量同一天, "
            "intercity_modes=['flight'] for 飞机往返; travel_modes refers only to local transport. "
            "For 每天每人250元 use budget.money.scope=per_person_per_day, not per_person or trip_total. "
            "Exact departure and return dates count inclusively as calendar days. If that conflicts with a prior duration, "
            "do not silently keep the old duration; return dates with calendar duration and mention the ambiguity in notes. "
            "Dislike spicy is not allergy; two people does not specify rooms. "
            "Resolve unambiguous mainland cities as country_code=CN, timezone=Asia/Shanghai, identity_status=candidate. "
            "Do not infer country for ambiguous destinations. Do not infer preferences from age/gender. "
            "If user says 暂不确定 put dates/budget into unknown_fields only when applicable; "
            "explicit refusal goes into declined_fields. Clear fields only at explicit user request. "
            "For unknown dates preserve explicitly stated duration_days. No arbitrary place IDs or fake reservations.",
            {"message": message, "current_intent": intent.model_dump(mode="json"), "repair": repair},
            IntakeResult.model_json_schema())
        parsed = IntakeResult.model_validate(result)
        # Guard a few unambiguous, easily dropped instructions from the chat.
        # Keep ambiguous dates and supplier facts in the model/question flow.
        patch = parsed.patch.model_dump(mode="json", exclude_unset=True)
        preferences = patch.get("preferences") or {}
        if "早起" in message and not re.search(r"不(?:想|喜欢|要)?早起", message):
            preferences["morning_style"] = "early"
        if re.search(r"(?:吃|要|安排)(?:早饭|早餐)", message) and not re.search(r"不(?:吃|要)(?:早饭|早餐)", message):
            preferences["breakfast_required"] = True
        if re.search(r"(?:飞机来回|往返飞机|飞机往返|坐飞机来回)", message):
            preferences["intercity_modes"] = ["flight"]
        if re.search(r"(?:邻近|附近|相邻).{0,16}(?:同一天|一天逛完|一天玩完)", message):
            preferences["compact_nearby"] = True
        if preferences:
            patch["preferences"] = preferences
        daily_budget = re.search(r"(?:每天每人|每人每天)(?:.{0,8}?)(\d+(?:\.\d+)?)\s*(?:元|块)", message)
        if "预算" in message and daily_budget:
            patch["budget"] = {"money": {"amount": daily_budget.group(1), "currency": "CNY",
                "scope": "per_person_per_day"}, "limit_kind": "hard" if "不能超过" in message else "target", "raw": message}
        return IntakeResult.model_validate({**parsed.model_dump(mode="json"), "patch": patch})
