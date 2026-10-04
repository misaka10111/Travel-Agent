"""从用户对话中提取旅行偏好摘要（轻量模型）。

用法：
  echo '{"conversation":[{"role":"user","content":"..."}, ...]}' \
    | python conversation_summary.py

输出：
  {"preferences": {"food": {...}, "hotel": {...}, "pace": "...", ...}}
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CONVERSATION_SUMMARY_PROMPT = (
    "你是用户旅行偏好提取助手。根据对话中用户说的话，提炼出稳定的旅行偏好，"
    "输出 JSON 对象，字段尽量与下面结构一致（没有的字段可以省略）："
    '{"food":{"cuisines":[],"avoid":[]},"hotel":{"must":[],"avoid":[]},'
    '"pace":"轻松或紧凑","transport":["打车优先"等],"liked":[],"avoided":[]}。'
    "只提取用户明确表达或反复提到的内容，不要臆测；避免用词精炼。"
    "只输出 JSON，不要任何多余文字。"
)


def summarize(conversation: list[dict]) -> dict:
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    user_messages = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in conversation
        if m.get("role") == "user"
    ]
    if not user_messages:
        return {}

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": CONVERSATION_SUMMARY_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"conversation": user_messages}, ensure_ascii=False
                ),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=600,
        timeout=60,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"preferences": {}}, ensure_ascii=False))
        return
    try:
        data = json.loads(raw)
        prefs = summarize(data.get("conversation") or [])
    except Exception as exc:  # noqa: BLE001
        prefs = {"error": str(exc)}
    print(json.dumps({"preferences": prefs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
