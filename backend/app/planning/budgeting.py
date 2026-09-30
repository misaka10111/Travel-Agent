"""Calculate evidence-backed partial spending references for a draft."""

from decimal import Decimal, InvalidOperation

from app.schemas.itinerary import CostSummary


def summarize_costs(days, places, intent):
    meals = [stop for day in days for stop in day.stops if stop.category == "restaurant"]
    subtotal = Decimal("0")
    covered = 0
    for stop in meals:
        place = places.get(stop.place_id)
        fact = place.facts.get("average_spend_cny") if place else None
        if not place or place.category != "restaurant" or not fact or not fact.source_ref.startswith("amap:poi:"):
            continue
        try:
            amount = Decimal(str(fact.value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite() or amount < 0:
            continue
        subtotal += amount
        covered += 1
    party = intent.party.count if intent.party.count_status == "exact" else None
    return CostSummary(planned_meals=len(meals), meals_with_reference=covered,
        food_reference_per_person=subtotal if covered else None,
        food_reference_for_party=subtotal * party if covered and party else None,
        party_count=party)
