"""Bounded HTTP requests with sanitized failures; no retries or response caching."""

from urllib.parse import urlsplit

import httpx

from app.providers.maps.base import MapError


class MapTransport:
    def __init__(self, *, max_calls: int = 12, timeout_seconds: float = 15, client: httpx.Client | None = None):
        if not 1 <= max_calls <= 30 or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid map request budget or timeout")
        self.max_calls = max_calls
        self.calls_used = 0
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(follow_redirects=False)

    def close(self):
        if self._owns_client:
            self._client.close()

    def request(self, method: str, url: str, **kwargs) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in {"restapi.amap.com", "places.googleapis.com", "routes.googleapis.com"}:
            raise MapError("invalid_request", "Map request host is not allowed.")
        if self.calls_used >= self.max_calls:
            raise MapError("budget_exhausted", "Map request count limit reached.")
        self.calls_used += 1
        try:
            response = self._client.request(method, url, timeout=self.timeout_seconds, follow_redirects=False, **kwargs)
        except httpx.RequestError:
            raise MapError("unavailable", "Map network request failed or timed out.", retryable=True) from None
        if response.status_code in (401, 403):
            raise MapError("permission_denied", "Map account or API permissions rejected the request.")
        if response.status_code == 429:
            raise MapError("rate_limited", "Map provider request quota was exceeded.", retryable=True)
        if response.status_code >= 500:
            raise MapError("unavailable", "Map provider service is unavailable.", retryable=True)
        if response.status_code != 200:
            raise MapError("invalid_request", "Map provider rejected the HTTP request.")
        try:
            data = response.json()
        except ValueError:
            raise MapError("invalid_response", "Map provider returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise MapError("invalid_response", "Map response must be an object.")
        return data
