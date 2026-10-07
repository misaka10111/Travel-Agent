"""从已确认的旅行计划中提取用户偏好摘要（轻量模型）。

用法：
  echo '{"plan": {...final_plan...}}' | python plan_summary.py

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

PLAN_SUMMARY_PROMPT = (
    "你是用户旅行偏好提取助手。根据一份已确认的旅行计划（含景点、酒店、餐厅、交通、总花费等），"
    "反推出用户的旅行偏好，输出 JSON 对象，字段尽量与下面结构一致（没有的字段可省略）："
    '{"food":{"cuisines":[],"avoid":[]},"hotel":{"must":[],"avoid":[],"star":""},'
    '"pace":"轻松或紧凑","transport":[],"liked":[],"avoided":[],"habits":[]}。'
    "food.cuisines 是计划里餐厅/美食体现出的菜系偏好；hotel.star 是住宿档次偏好（经济/舒适/豪华）；"
    "transport 是出行方式偏好（如高铁、飞机、打车）；liked/avoided 是景点偏好；habits 是旅行习惯（如慢游、早起、必吃当地特色、偏好免费景点等）。"
    "只依据计划内容提炼，不要臆测；用词精炼。只输出 JSON，不要任何多余文字。"
)


def summarize_plan(plan: dict) -> dict:
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    model = os.getenv("OPENAI_MODEL", "deepseek-flash")
    model_options = {"extra_body": {"enable_thinking": False}} if "deepseek" in model.lower() or "qwen" in model.lower() else {}
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PLAN_SUMMARY_PROMPT},
            {"role": "user", "content": json.dumps({"plan": plan}, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        max_tokens=600,
        timeout=60,
        **model_options,
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
        prefs = summarize_plan(data.get("plan") or {})
    except Exception as exc:  # noqa: BLE001
        prefs = {"error": str(exc)}
    print(json.dumps({"preferences": prefs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
