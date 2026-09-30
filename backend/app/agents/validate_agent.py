from typing import Literal

from pydantic import Field

from app.schemas.common import ContractModel
from app.schemas.itinerary import PlanningIssue


class ExperienceReview(ContractModel):
    status: Literal["passed", "failed", "unknown"]
    issues: list["ExperienceIssue"] = Field(default_factory=list, max_length=5)


class ExperienceIssue(ContractModel):
    kind: Literal["quality", "missing_evidence"]
    message: str


class ValidateAgent:
    def __init__(self, client):
        self.client = client

    async def review(self, plan, intent, candidates):
        result = await self.client.complete(
            "Review travel pace, preference fit, geographic detours and explanation quality. Return JSON. "
            "Do not override hard constraints or claim unknown hours/menus/inventory are known. "
            "A dietary match without menu evidence is unknown, not confirmed. Hotel returns are normal. "
            "Give at most 5 specific actionable problems. No unsupported facts or invented prices.",
            {"intent": intent.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
             "places": [p.model_dump(mode="json") for p in candidates if p.place_id in {s.place_id for d in plan.days for s in d.stops}]},
            ExperienceReview.model_json_schema())
        review = ExperienceReview.model_validate(result)
        audit = plan.audit.model_copy(deep=True)
        # Missing evidence is unknown, not a quality failure that can only be
        # 'repaired' by inventing dates/menus. Hard violations remain untouched.
        quality_failures = [i for i in review.issues if i.kind == "quality"]
        audit.experience_status = "failed" if review.status == "failed" and quality_failures else ("unknown" if review.status == "unknown" or any(i.kind == "missing_evidence" for i in review.issues) else "passed")
        audit.reviewed = True
        audit.issues.extend(PlanningIssue(code="experience", severity="unknown" if i.kind == "missing_evidence" else ("blocking" if audit.experience_status == "failed" else "warning"), message=i.message) for i in review.issues)
        return audit
