"""Bounded JSON Chat Completions; provider bodies are never logged or persisted."""

import json
from urllib.parse import urlsplit

import httpx

from app.runtime.guards import PlanningError


def model_context(value):
    """Route geometry belongs to rendering, not language-model decisions.

    Keep identity, coordinates, constraints, times, evidence and errors intact.
    This is a projection only; the UI still receives full route geometry.
    """
    if isinstance(value, list):
        return [model_context(item) for item in value]
    if isinstance(value, dict):
        return {key: model_context(item) for key, item in value.items() if key != "geometry"}
    return value


class JsonModelClient:
    def __init__(self, settings):
        self.settings = settings

    async def complete(self, system, context, schema):
        key = self.settings.planning_api_key
        if not key or not key.get_secret_value():
            raise PlanningError("model_missing_credentials")
        base = self.settings.planning_base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PlanningError("model_invalid_base_url")
        body = {"model": self.settings.planning_model, "temperature": 0.2,
            "max_tokens": self.settings.planning_max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\nReturn one JSON object following this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(model_context(context), ensure_ascii=False)}]}
        if parsed.hostname == "api.deepseek.com":
            # The provider defaults to thinking=enabled; keep small action emissions explicit.
            body["thinking"] = {"type": self.settings.planning_thinking_mode}
        try:
            async with httpx.AsyncClient(timeout=self.settings.planning_model_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(base + "/chat/completions",
                    headers={"Authorization": "Bearer " + key.get_secret_value()},
                    json=body)
                if response.status_code != 200:
                    code = "model_permission_denied" if response.status_code in (401, 403) else "model_request_failed"
                    raise PlanningError(code)
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise PlanningError("model_output_truncated")
                output = choice["message"]["content"]
                if not isinstance(output, str) or not output.strip():
                    raise PlanningError("model_empty_content")
                data = json.loads(output)
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except httpx.RequestError:
            raise PlanningError("model_unavailable_or_timeout") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise PlanningError("model_invalid_json") from None
