"""PlanAgent：读取 SearchAgent 的结构化输出 + 用户画像，生成逐日旅行计划。

用法：
  # 先跑 SearchAgent 拿到结构化结果，再喂给 PlanAgent
  echo '{"destination":"宁波","start_date":"2026-10-01","end_date":"2026-10-03"}' \
    | python ../SearchAgent/search.py \
    | python plan.py

输入（三选一）：
  1) SearchAgent 返回的 JSON（weather/hotels/poi/promotions），不带画像/作答
  2) {"profile": {...用户画像...}, "search": {...搜索结果...}}
  3) {"profile": {...}, "search": {...}, "answers": [{"question":"...","answer":"..."}]}
输出：旅行计划 JSON（含逐日 itinerary）
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PLAN_SYSTEM_PROMPT = (
    "你是一名专业的旅行规划师。根据输入中的结构化旅行数据（search 字段）和用户画像（user_profile 字段，可能没有），"
    "生成一个旅行方案。方案风格由用户消息中的 style 字段指定，必须鲜明体现该风格。"
    "输出必须是 JSON，结构如下："
    '{"style":"风格","summary":"一句话概述","itinerary":['
    '{"day":1,"date":"日期","theme":"当天主题","hotel":"推荐酒店","schedule":['
    '{"time":"09:00-11:00","type":"景点","name":"活动名","note":"交通/餐食/穿衣等简短说明"}'
    ']}]}。'
    "如果输入中包含 user_profile（用户画像），其字段含义为："
    "age_group=年龄段；gender=性别；identity=身份（学生/上班族/自由职业/创业者/退休/其他）；city=常驻城市；"
    "travel_style=旅行风格（可多选：休闲度假/深度文化/自然风光/美食探店/亲子乐园/购物血拼/冒险户外/摄影旅拍）。"
    "如果输入中包含 answers（用户对问卷的作答，每项含 question 和 answer），请优先严格遵循用户的选择来规划"
    "（已选的景点、酒店、出行方式、预算侧重等），未作答的项再按画像和常识默认。"
    "如果输入中包含 feedback（上次审核的修改建议），必须据此修正计划中列出的问题。"
    "如果输入中包含 modify（含 block_id 和 instruction），按 instruction 修改对应那一个活动块，其余块尽量保持不变。"
    "个性化与规划规则："
    "1) 每天固定 2 个景点/活动；"
    "2) schedule 每天最多 4 项：2 个景点（type=景点）、午餐（type=美食）、晚餐（type=美食）；交通方式合并进景点 note，不再单独列交通项；"
    "3) 景点优先匹配 travel_style（自然风光→自然景区；亲子乐园→主题乐园/动物园；深度文化→博物馆/古迹；购物血拼→商圈；摄影旅拍→出片景点；冒险户外→户外体验）；"
    "4) 优先使用 search 数据里真实存在的景点和酒店名称；"
    "5) 每个 note 控制在 15 字以内；"
    "6) 只输出 JSON，不要任何多余文字或代码块。"
)


def _build_meta(search_result: dict) -> dict:
    """从搜索结果本地提取行程元数据，省掉 LLM 生成外层字段。"""
    destination = search_result.get("destination") or ""
    start_date = search_result.get("start_date") or ""
    end_date = search_result.get("end_date") or ""
    days = 0
    if start_date and end_date:
        try:
            days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
        except ValueError:
            days = 0
    weather = search_result.get("weather") or {}
    weather_days = weather.get("days") or []
    weather_summary = ""
    if weather_days:
        first = weather_days[0]
        weather_summary = (
            f"{weather.get('location', destination)} {start_date} 至 {end_date}，"
            f"首日{first.get('weather', '')}，共 {len(weather_days)} 天"
        )
    return {
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "weather_summary": weather_summary,
    }


def _trim_search(search: dict) -> dict:
    """精简 search 结果：去掉长文本与无关字段，降低 PlanAgent 的 prompt 长度。"""
    result: dict = {}
    for key in ("destination", "start_date", "end_date"):
        if search.get(key):
            result[key] = search[key]

    weather = search.get("weather")
    if isinstance(weather, dict):
        result["weather"] = {
            "location": weather.get("location"),
            "days": [
                {k: d.get(k) for k in ("date", "weather", "temp_min", "temp_max", "humidity")}
                for d in (weather.get("days") or [])
            ],
        }

    if isinstance(search.get("poi"), list):
        result["poi"] = [
            {
                "name": p.get("name"),
                "category": p.get("category"),
                "rank": p.get("rank"),
                "free": p.get("free"),
                "description": (p.get("description") or "")[:80],
            }
            for p in search["poi"]
        ]

    if isinstance(search.get("hotels"), list):
        result["hotels"] = [
            {"name": h.get("name"), "star": h.get("star"), "price": h.get("price"), "location": h.get("location")}
            for h in search["hotels"]
        ]

    if isinstance(search.get("promotions"), list):
        result["promotions"] = [
            {"title": p.get("title"), "price": p.get("price")}
            for p in search["promotions"]
        ]

    for key in ("events", "food"):
        if isinstance(search.get(key), list):
            result[key] = [
                {"title": x.get("title"), "content": (x.get("content") or "")[:120]}
                for x in search[key]
            ]

    return result


def _build_one_plan(client: OpenAI, style: str, context: dict) -> dict:
    ctx = dict(context)
    ctx["style"] = style
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        max_tokens=16000,
        timeout=300,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)


def build_plan(
    search_result: dict,
    profile: dict | None = None,
    answers: list | None = None,
    feedback: str | None = None,
    modify: dict | None = None,
) -> dict:
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    context: dict = {"search": _trim_search(search_result)}
    if profile:
        context["user_profile"] = profile
    if answers:
        context["answers"] = answers
    if feedback:
        context["feedback"] = feedback
    if modify:
        context["modify"] = modify

    styles = ["经典人气", "小众深度"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        plans = list(executor.map(lambda s: _build_one_plan(client, s, context), styles))

    result = _build_meta(search_result)
    result["plans"] = plans
    return result


TYPE_KEYWORDS = (
    ("交通", ["交通", "地铁", "打车", "乘车", "前往", "返回", "接送", "出发", "抵达", "步行", "自驾", "专线", "换乘", "车站", "车程", "高铁"]),
    ("美食", ["早餐", "午餐", "晚餐", "餐厅", "饭店", "美食", "小吃", "菜馆", "面馆"]),
    ("酒店", ["入住", "退房", "住宿", "酒店", "民宿", "客栈"]),
    ("景点", ["博物院", "博物馆", "公园", "乐园", "景区", "广场", "老街", "外滩", "湿地", "寺", "塔", "影视城", "动物园", "海洋", "湖", "阁", "书院", "山"]),
)


def _infer_type(name: str, note: str) -> str:
    text = f"{name} {note}"
    for typ, keywords in TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return typ
    return "活动"


def blockify(plan: dict) -> list[dict]:
    """把嵌套的 plans[].itinerary[].schedule[] 拍平成扁平的 blocks 列表。"""
    blocks: list[dict] = []
    counter = 0
    for p in plan.get("plans") or []:
        style = p.get("style") or ""
        for it in p.get("itinerary") or []:
            day = it.get("day")
            date_ = it.get("date") or ""
            has_hotel_block = False
            for item in it.get("schedule") or []:
                counter += 1
                name = item.get("name") or ""
                note = item.get("note") or ""
                typ = item.get("type") or _infer_type(name, note)
                if typ == "酒店":
                    has_hotel_block = True
                blocks.append(
                    {
                        "id": f"b{counter}",
                        "plan_style": style,
                        "day": day,
                        "date": date_,
                        "type": typ,
                        "time": item.get("time") or "",
                        "name": name,
                        "note": note,
                    }
                )
            for meal in it.get("meals") or []:
                counter += 1
                meal_name = meal.get("meal") or ""
                options = [str(o) for o in (meal.get("options") or [])]
                blocks.append(
                    {
                        "id": f"b{counter}",
                        "plan_style": style,
                        "day": day,
                        "date": date_,
                        "type": "美食",
                        "time": meal_name,
                        "name": " / ".join(options) if options else meal_name,
                        "note": "餐饮推荐",
                    }
                )
            hotel = it.get("hotel")
            if hotel and not has_hotel_block:
                counter += 1
                blocks.append(
                    {
                        "id": f"b{counter}",
                        "plan_style": style,
                        "day": day,
                        "date": date_,
                        "type": "酒店",
                        "time": "住宿",
                        "name": hotel,
                        "note": "推荐住宿",
                    }
                )
    return blocks


def main() -> None:
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    else:
        path = sys.argv[1] if len(sys.argv) > 1 else ""
        if path and os.path.exists(path):
            raw = Path(path).read_text(encoding="utf-8").strip()
        else:
            raw = " ".join(sys.argv[1:]).strip()

    if not raw:
        print(json.dumps({"error": "empty input"}, ensure_ascii=False))
        return

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and any(k in data for k in ("search", "profile", "answers", "feedback", "modify")):
            search_result = data.get("search") or {}
            profile = data.get("profile")
            answers = data.get("answers")
            feedback = data.get("feedback")
            modify = data.get("modify")
        else:
            search_result = data
            profile = None
            answers = None
            feedback = None
            modify = None
        plan = build_plan(search_result, profile, answers, feedback, modify)
        if isinstance(plan, dict) and "error" not in plan:
            plan["blocks"] = blockify(plan)
    except Exception as exc:  # noqa: BLE001
        plan = {"error": str(exc)}

    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
