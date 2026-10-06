"""QuestionAgent：补全旅行信息 + 判断修改意图。

输入（stdin JSON）：
  {"messages":[{"role":"user|assistant","content":"..."}], "has_plan": false, "trip_data": {...}}

输出（stdout JSON）：
  {"action":"ask","field":"trip_details","missing":[...],"data":{...},"question":"...","options":[]}
  {"action":"plan","data":{destination,start_date,end_date,origin,travelers,budget_tiers,total_budget,purposes}}
  {"action":"modify","mode":"global|block","targets":[...],"instruction":"..."}
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from trip_intent import complete_trip_request

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# 共用规划模型配置；当前项目没有单独的 QuestionAgent/.env。
load_dotenv(BASE_DIR.parent / "PlanAgent" / ".env")

SYSTEM_PROMPT = (
    "你是旅行需求识别助手。阅读完整对话和 trip_data 中已经提取的信息，识别用户的自由输入，输出 JSON。\n"
    "1) 用户想规划旅行或补充旅行信息时，输出 action=collect，data 为已知旅行参数对象。"
    "用户可以一次提供多项信息，也可以只补充一项；提取所有明确表达的参数，最新修正覆盖旧信息。"
    "字段包括 destination(具体目的地城市)、origin(出发城市)、start_date/end_date(YYYY-MM-DD)、"
    "duration_days(游玩天数，整数)、travelers(总人数，如3人)、total_budget(全体出行者本次旅行总预算，元)、"
    "budget_per_person(明确为每人整趟预算时填写，元)、budget_unlimited(明确不限制预算时为true)、"
    "budget_tiers(可选，经济/舒适/豪华/不设限数组)、purposes(兴趣/旅行目的数组)、"
    "requested_pois(用户点名的景点数组)、food_keyword(明确的菜系或餐厅关键词)、"
    "notes(交通、住宿、节奏、饮食忌口等其他要求)。"
    "例如两大一小总人数为3人，预算一万为10000元；必须区别总预算和每人预算。"
    "相对日期如明天、下周五根据提供的今天日期换算；有出发日期和游玩天数时可计算返程日期。"
    "仅说玩几天但没说何时出发，不得默认为今天；没说返程日期或天数不得默认三天。"
    "没有表达的信息省略，严禁自行假定目的地、日期、人数、金额。预算档位和兴趣均为可选，不要主动追问。"
    "只从用户消息和 trip_data 提取事实，助手的举例或建议不代表用户已选择。"
    "不要生成选择题或 options；缺失信息由程序校验并提示文字补充。"
    "如果已有计划但用户明确要另一次旅行（如重新规划、换目的地），输出 new_trip=true，"
    "data 只提取这次新旅行的信息。\n"
    "2) 如果 has_plan=true 且用户是在修改已有方案，先归纳修改意图，输出："
    '{"action":"confirm","summary":"我理解你是想……","mode":"global|block","targets":[...],"instruction":"用户原意"}。'
    "具体景点/酒店/活动→block；整体意见→global。"
    "当用户在后续对话中明确确认（例如「对」「可以」「确认」）时，输出："
    '{"action":"modify","mode":"...","targets":[...],"instruction":"..."}。'
    "当用户否认或补充（例如「不对，应该是……」）时，继续输出 action=confirm 更新归纳。"
    "3) 如果用户只是询问某个地点/景点（例如「宽窄巷子是玩啥的」「这个景点怎么样」），"
    '输出 {"action":"explain","answer":"简短解释","query":"宽窄巷子"}，不要规划行程。'
    "只输出 JSON，不要任何多余文字。"
)


def resolve_intent(data: dict, client: OpenAI | None = None) -> dict:
    messages = data.get("messages") or []
    has_plan = bool(data.get("has_plan"))
    trip_data = data.get("trip_data") or {}
    today = date.today()
    if not messages:
        return {"error": "请输入旅行需求"}

    if client is None:
        kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
        if os.getenv("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
        client = OpenAI(**kwargs)

    model = os.getenv("OPENAI_MODEL", "deepseek-flash")
    model_options = {"extra_body": {"enable_thinking": False}} if model.lower().startswith("qwen") else {}
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": f"今天是 {today.isoformat()}。\n{SYSTEM_PROMPT}"},
            {
                "role": "user",
                "content": json.dumps(
                    {"messages": messages, "has_plan": has_plan, "trip_data": trip_data},
                    ensure_ascii=False,
                ),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=2000,
        timeout=60,
        **model_options,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    result = json.loads(content)
    if not isinstance(result, dict):
        return {"error": "识别结果格式异常，请重新描述需求"}
    if result.get("action") in ("collect", "plan", "ask"):
        extracted = result.get("data")
        if not isinstance(extracted, dict):
            return {"error": "未能识别旅行信息，请用文字补充目的地、时间等需求"}
        previous = {} if result.get("new_trip") is True else trip_data
        return complete_trip_request(extracted, previous, today)
    if result.get("action") in ("explain", "confirm", "modify"):
        return result
    return {"error": "未能识别这条需求，请重新描述"}


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        result = resolve_intent(json.loads(raw)) if raw else {"error": "empty input"}
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
