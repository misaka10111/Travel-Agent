"""Bounded JSON Chat Completions; provider bodies are never logged or persisted."""

import json
from urllib.parse import urlsplit

import httpx

from app.runtime.guards import PlanningError


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
        try:
            async with httpx.AsyncClient(timeout=self.settings.planning_model_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(base + "/chat/completions",
                    headers={"Authorization": "Bearer " + key.get_secret_value()},
                    json={"model": self.settings.planning_model, "temperature": 0.2, "max_tokens": 2200,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": system + "\nReturn one JSON object following this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]})
                if response.status_code != 200:
                    code = "model_permission_denied" if response.status_code in (401, 403) else "model_request_failed"
                    raise PlanningError(code)
                output = response.json()["choices"][0]["message"]["content"]
                data = json.loads(output)
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except httpx.RequestError:
            raise PlanningError("model_unavailable_or_timeout") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise PlanningError("model_invalid_json") from None
