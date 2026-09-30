from app.planning.clustering import meters
from app.providers.maps.base import MapError, RouteQuery, route_result
from app.schemas.common import utc_now


class RoutePlanner:
    def __init__(self, provider, intent, limit, prior_legs=()):
        self.provider, self.intent, self.limit = provider, intent, limit
        self.calls = 0
        self.failed_code = None
        # Same-session, in-memory reuse only. Identical endpoints, mode and
        # departure conditions are required; errors and stale estimates expire.
        self.memo = {(r.from_place_id, r.to_place_id, r.mode, r.requested_departure_at.isoformat()): r
            for r in prior_legs if r.status == "ok" and 0 <= (utc_now() - r.queried_at).total_seconds() < 3600}

    def leg(self, origin, destination, departure):
        allowed = list(dict.fromkeys(self.intent.preferences.travel_modes or ["walking", "transit"]))
        short = meters(origin, destination) < 900
        primary = "walking" if short and "walking" in allowed else next((m for m in allowed if m != "walking"), "walking")
        # Never add a mode the user did not allow. Long walks are considered
        # only when walking is the sole option; short ones can rescue no-route.
        alternatives = [m for m in allowed if m != primary and (m != "walking" or meters(origin, destination) < 1500)]
        modes = [primary] + alternatives
        for index, mode in enumerate(modes):
            query = RouteQuery(origin=origin, destination=destination, mode=mode, departure_at=departure)
            key = (origin.place_id, destination.place_id, mode, departure.isoformat())
            if key in self.memo:
                result = self.memo[key].model_copy(deep=True)
            elif self.calls >= self.limit or self.failed_code:
                return route_result(query, "amap", status="unknown", unknown_reason=self.failed_code or "route_phase_budget_exhausted")
            else:
                self.calls += 1
                try:
                    result = self.provider.route(query)
                except MapError as exc:
                    if exc.code in {"permission_denied", "missing_credentials", "rate_limited", "budget_exhausted", "unavailable"}:
                        self.failed_code = exc.code
                    status = exc.code if exc.code in {"permission_denied", "rate_limited", "unavailable", "unsupported"} else "unknown"
                    result = route_result(query, "amap", status=status, unknown_reason=exc.code)
                self.memo[key] = result
            if result.status not in {"no_route", "unsupported"} or index == len(modes) - 1:
                return result
