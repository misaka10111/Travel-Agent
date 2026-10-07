"""把最近的历史行程摘要成稳定偏好与避雷点（轻量模型）。

用法：
  echo '{"recent_trips":[{"destination":"杭州",...}]}' | python trip_summary.py

输出：
  {"summary": "偏好：...；避雷：...；节奏：..."}
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TRIP_SUMMARY_PROMPT = (
    "你是用户旅行记忆摘要助手。根据给定的历史行程记录（recent_trips），"
    "提炼出对后续旅行规划有用的稳定偏好和教训。"
    "重点关注：用户选择的方案风格、评分与反馈（rating/feedback）、用户主动修改（user_edits）所透露的喜好与避雷点。"
    "输出一段不超过 150 字的摘要，用「偏好：...；避雷：...；节奏：...」的简洁形式。"
    "只输出摘要文字，不要 JSON、不要解释。"
)


def _model_options() -> dict:
    model = os.getenv("OPENAI_MODEL", "qwen3.8-27b").lower()
    if "qwen" in model or "deepseek" in model:
        return {"extra_body": {"enable_thinking": False}}
    return {}


def summarize_trips(recent_trips: list) -> str:
    if not recent_trips:
        return ""
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY"), "max_retries": 0}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "qwen3.8-27b"),
        messages=[
            {"role": "system", "content": TRIP_SUMMARY_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"recent_trips": recent_trips}, ensure_ascii=False
                ),
            },
        ],
        max_tokens=300,
        timeout=60,
        **_model_options(),
    )
    return (resp.choices[0].message.content or "").strip()


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"summary": ""}, ensure_ascii=False))
        return
    try:
        data = json.loads(raw)
        summary = summarize_trips(data.get("recent_trips") or [])
    except Exception:  # noqa: BLE001
        summary = ""
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
