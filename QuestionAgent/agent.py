"""QuestionAgent：补全旅行信息 + 判断修改意图。

输入（stdin JSON）：
  {"messages":[{"role":"user|assistant","content":"..."}], "has_plan": false}

输出（stdout JSON）：
  {"action":"ask","field":"origin","question":"您准备从哪里出发？","options":[]}
  {"action":"plan","data":{destination,start_date,end_date,origin,travelers,budget_tiers,total_budget,purposes}}
  {"action":"modify","mode":"global|block","targets":[...],"instruction":"..."}
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SYSTEM_PROMPT = (
    "你是旅行信息补全与修改意图判断助手。"
    "根据对话历史判断当前任务，输出 JSON：\n"
    "1) 如果用户是在首次规划，且旅行信息不完整，逐步补全以下字段："
    "destination(目的地)、start_date(YYYY-MM-DD)、end_date(YYYY-MM-DD)、origin(出发地)、"
    "travelers(人数)、budget_tiers(预算档位)、total_budget(总预算金额)、purposes(旅行目的数组)。"
    "一次只问一个缺失字段。输出："
    '{"action":"ask","field":"origin","question":"您准备从哪里出发？","options":[]}。'
    "options 可给候选选项，文本类字段给空数组。"
    "2) 如果信息已完整，输出："
    '{"action":"plan","data":{destination,start_date,end_date,origin,travelers,budget_tiers,total_budget,purposes,requested_pois}}。'
    "requested_pois 是用户明确点名想去的地点名称数组（如 [\"西湖\",\"良渚博物院\"]），没有则给空数组。"
    "3) 如果输入 has_plan=true，说明用户是在修改已有方案，不再补全信息。先归纳修改意图，输出："
    '{"action":"confirm","summary":"我理解你是想……","mode":"global|block","targets":[...],"instruction":"用户原意"}。'
    "具体景点/酒店/活动→block；整体意见→global。"
    "当用户在后续对话中明确确认（例如「对」「可以」「确认」）时，输出："
    '{"action":"modify","mode":"...","targets":[...],"instruction":"..."}。'
    "当用户否认或补充（例如「不对，应该是……」）时，继续输出 action=confirm 更新归纳。"
    "4) 如果用户只是询问某个地点/景点（例如「宽窄巷子是玩啥的」「这个景点怎么样」），"
    '输出 {"action":"explain","answer":"简短解释","query":"宽窄巷子"}，不要规划行程。'
    "只输出 JSON，不要任何多余文字。"
)


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"error": "empty input"}, ensure_ascii=False))
        return
    data = json.loads(raw)
    messages = data.get("messages") or []
    has_plan = bool(data.get("has_plan"))

    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"messages": messages, "has_plan": has_plan},
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
            timeout=60,
        )
        content = (resp.choices[0].message.content or "{}").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
