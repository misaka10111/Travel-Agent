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
        allowed = self.intent.preferences.travel_modes or ["walking", "transit"]
        mode = "walking" if "walking" in allowed and meters(origin, destination) < 900 else next((m for m in allowed if m != "walking"), "walking")
        query = RouteQuery(origin=origin, destination=destination, mode=mode, departure_at=departure)
        key = (origin.place_id, destination.place_id, mode, departure.isoformat())
        if key in self.memo:
            return self.memo[key].model_copy(deep=True)
        if self.calls >= self.limit or self.failed_code:
            return route_result(query, "amap", status="unknown", unknown_reason=self.failed_code or "route_phase_budget_exhausted")
        self.calls += 1
        try:
            result = self.provider.route(query)
            # Short walks may have no transit option. Query a real walking route,
            # never turn a straight-line estimate into a usable route.
            if result.status == "no_route" and mode == "transit" and "walking" in allowed and meters(origin, destination) < 1500 and self.calls < self.limit:
                self.calls += 1
                query = query.model_copy(update={"mode": "walking"})
                result = self.provider.route(query)
        except MapError as exc:
            if exc.code in {"permission_denied", "missing_credentials", "rate_limited", "budget_exhausted", "unavailable"}:
                self.failed_code = exc.code
            status = exc.code if exc.code in {"permission_denied", "rate_limited", "unavailable", "unsupported"} else "unknown"
            result = route_result(query, "amap", status=status, unknown_reason=exc.code)
        self.memo[key] = result
        return result
