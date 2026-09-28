"""SearchAgent 的统一入口：JSON 或自然语言输入 → 结构化 JSON 输出。

用法（JSON 输入）：
  echo '{"destination":"宁波","start_date":"2026-10-01","end_date":"2026-10-05"}' | python search.py

用法（自然语言输入，会先用 LLM 解析成 JSON）：
  python search.py "宁波 10月1日到10月5日"
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import tools

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PARSE_SYSTEM_PROMPT = (
    f"今天是 {date.today().strftime('%Y-%m-%d')}。"
    "从用户的旅行查询中抽取信息，输出 JSON，字段："
    "destination（目的地，字符串，必填）、"
    "start_date（开始日期，格式 YYYY-MM-DD，必填）、"
    "end_date（结束日期，格式 YYYY-MM-DD，可选）、"
    "origin（出发地，可选）、"
    "travelers（出行人数，字符串，可选，如 2人）、"
    "budget（预算金额，数字，可选，如 5000）、"
    "purposes（旅行目的，字符串数组，可选，如 [\"美食\",\"文化\"]）。"
    f"如果用户只给了时长（如「玩3天」）而没有具体日期，则 start_date 默认为今天（{date.today().strftime('%Y-%m-%d')}），end_date 为 start_date 加上对应天数；"
    "如果日期没有年份，默认今年。"
    "只输出 JSON 本身，不要任何多余文字或代码块。"
)


def parse_nl(query: str) -> dict:
    """用 LLM 把自然语言解析成结构化 JSON。"""
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        response_format={"type": "json_object"},
        reasoning_effort="low",
        timeout=60,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    # 去掉可能包裹的 ```json ... ``` 代码块
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    result = json.loads(content)
    today = date.today()

    def is_valid_day(value: str | None) -> bool:
        try:
            return bool(value) and date.fromisoformat(value) >= today
        except (ValueError, TypeError):
            return False

    # 兜底：日期缺失或为过去日期时，默认今天起 3 天
    if not is_valid_day(result.get("start_date")):
        result["start_date"] = today.isoformat()
        result["end_date"] = (today + timedelta(days=2)).isoformat()
    elif not result.get("end_date"):
        # end_date 缺失时，默认 3 天行程（start + 2 天），避免只 1 天导致酒店入离校验失败
        result["end_date"] = (
            date.fromisoformat(result["start_date"]) + timedelta(days=2)
        ).isoformat()

    return result


def main() -> None:
    if "--parse" in sys.argv:
        query = (
            sys.stdin.read().strip()
            if not sys.stdin.isatty()
            else " ".join(a for a in sys.argv[1:] if a != "--parse").strip()
        )
        try:
            print(json.dumps(parse_nl(query), ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return

    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    else:
        raw = " ".join(sys.argv[1:]).strip()

    if not raw:
        print(json.dumps({"error": "empty input"}, ensure_ascii=False))
        return

    # 先按 JSON 解析，失败则按自然语言解析
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            data = parse_nl(raw)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": f"自然语言解析失败：{exc}"}, ensure_ascii=False))
            return

    try:
        result = tools.run_search(data)
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
