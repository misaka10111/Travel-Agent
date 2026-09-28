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
    "以及本次旅行基本信息（basic 字段，可能没有），生成一个旅行方案。方案风格由用户消息中的 style 字段指定，必须鲜明体现。"
    "输出必须是 JSON，结构如下："
    '{"style":"风格","summary":"一句话概述","itinerary":['
    '{"day":1,"date":"日期","theme":"当天主题","hotel":"推荐酒店","schedule":['
    '{"time":"09:00-11:00","type":"景点","name":"活动名","note":"交通/餐食/穿衣等简短说明"}'
    ']}]}。'
    "如果输入中包含 user_profile（用户画像），其字段含义为："
    "age_group=年龄段；gender=性别；identity=身份；city=常驻城市；"
    "travel_style=旅行风格（可多选：休闲度假/深度文化/自然风光/美食探店/亲子乐园/购物血拼/冒险户外/摄影旅拍）。"
    "如果输入中包含 basic（本次旅行基本信息），其字段含义为："
    "origin=出发地；travelers=出行人数；budget_tiers=预算档位；total_budget=总预算金额；purposes=旅行目的（可多选）。"
    "根据画像和旅行信息动态规划每日节奏与内容："
    "1) 景点数量：休闲度假/带老人→每天2个；深度文化/冒险户外/年轻单人→每天3个；其余2~3个；"
    "2) 景点形式按 travel_style 匹配：美食→餐饮街/老字号；亲子→乐园/动物园/海洋馆；文化→博物馆/古迹/艺术馆；"
    "自然→山水园林/湖景；购物→商圈；摄影→出片打卡点；冒险→徒步/户外体验；"
    "3) 美食：travel_style 含「美食探店」或 purposes 含「美食之旅」时，午晚餐各给具体饭店名；否则每餐1家即可；"
    "4) 预算：经济档优先免费景点+公共交通，豪华档可付费体验+打车；总花费不超 total_budget；"
    "5) 人数与身份：带老人/孩子减少步行、安排休息点；学生控预算；退休轻松节奏；"
    "6) 交通：相邻景点在 note 里标注交通方式+大致耗时（打车/地铁/步行），尽量地理就近、少折返；"
    "7) 天气：雨天优先室内景点（博物馆/乐园室内馆），晴天可户外；"
    "8) 去程/回程：若 search 含 flights/trains，第一天 schedule 开头加一个 type=交通 的去程项"
    "（name 取 flights/trains 的 outbound 里的一项，note 写「出发地→目的地 出发时间 价格」），"
    "最后一天结尾加一个 type=交通 的回程项（name 取 flights/trains 的 inbound 里的一项，反向）；"
    "9) schedule 每天 4~6 项（景点+午晚餐+必要交通），每项 note ≤20 字；优先用 search 里的真实景点/酒店/饭店名称；"
    "10) 有 answers 时严格遵循；有 feedback 时修正；若输入含 modify（block_ids 数组 + instruction 文本），"
    "只按 instruction 修改 block_ids 对应的那些活动块，其余块保持不变，并返回完整计划；"
    "11) 只输出 JSON，不要任何多余文字或代码块。"
)


MODIFY_PROMPT = (
    "你是旅行计划修改助手。根据 instruction 修改给定的 blocks（活动块）。"
    "保持每个 block 的字段结构不变（id/day/date/type/time/name/note/link），只修改 instruction 要求的字段。"
    "若 instruction 涉及酒店档次、景点类型等，可参考 search 数据里的真实名称替换；"
    "instruction 未涉及的 block 保持原样。"
    "输出 JSON：{\"blocks\":[修改后的 block...]}，顺序与输入一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
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

    for key in ("flights", "trains"):
        if isinstance(search.get(key), list):
            outbound = []
            inbound = []
            for x in search[key]:
                if key == "flights":
                    name = f"{x.get('airline') or ''}{x.get('flight_no') or ''}"
                else:
                    name = f"{x.get('transport') or ''}{x.get('train_no') or ''}"
                item = {
                    "name": name,
                    "from": x.get("dep_station") or "",
                    "to": x.get("arr_station") or "",
                    "time": x.get("dep_time") or "",
                    "price": x.get("price") or "",
                }
                if x.get("direction") == "回":
                    inbound.append(item)
                else:
                    outbound.append(item)
            result[key] = {"outbound": outbound[:3], "inbound": inbound[:3]}

    return result


def _backfill_links(plan: dict, search: dict) -> dict:
    """生成后按名称匹配回填链接，避免 url 进 prompt 导致 prompt 过长。"""
    entries: list[tuple[str, str]] = []
    for key in ("hotels", "poi", "food", "events", "promotions"):
        items = search.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            name = item.get("name") or item.get("title") or ""
            url = item.get("url") or ""
            if name and url:
                entries.append((name, url))
    for key in ("flights", "trains"):
        items = search.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if key == "flights":
                name = f"{item.get('airline') or ''}{item.get('flight_no') or ''}"
            else:
                name = f"{item.get('transport') or ''}{item.get('train_no') or ''}"
            url = item.get("url") or ""
            if name and url:
                entries.append((name, url))

    def find_url(target: str) -> str:
        if not target:
            return ""
        for name, url in entries:
            if name == target:
                return url
        # 前缀/包含匹配：取最长的匹配名，避免「酒店名 + 房型后缀」匹配不上
        best = ""
        best_len = 0
        for name, url in entries:
            if target.startswith(name) or name.startswith(target):
                if len(name) > best_len:
                    best = url
                    best_len = len(name)
        return best

    for p in plan.get("plans") or []:
        for it in p.get("itinerary") or []:
            hotel = it.get("hotel") or ""
            it["hotel_link"] = find_url(hotel)
            for s in it.get("schedule") or []:
                name = s.get("name") or ""
                s["link"] = find_url(name)
    return plan


def _build_one_plan(client: OpenAI, style: str, context: dict) -> dict:
    ctx = dict(context)
    ctx["style"] = style
    last: dict = {}
    for _ in range(2):
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
        try:
            last = json.loads(content)
        except json.JSONDecodeError:
            last = {}
        if isinstance(last, dict) and last.get("itinerary"):
            return last
    return last


def build_plan(
    search_result: dict,
    profile: dict | None = None,
    basic: dict | None = None,
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
    if basic:
        context["basic"] = basic
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
    return _backfill_links(result, search_result)


def modify_blocks(
    blocks: list[dict],
    instruction: str,
    search_result: dict | None = None,
    profile: dict | None = None,
    basic: dict | None = None,
) -> dict:
    """局部修改：只修改选中的 block，输出改完后的 blocks 列表。"""
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    context: dict = {"blocks": blocks, "instruction": instruction}
    if search_result:
        context["search"] = _trim_search(search_result)
    if profile:
        context["user_profile"] = profile
    if basic:
        context["basic"] = basic

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": MODIFY_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        max_tokens=3000,
        timeout=120,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)


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
                        "link": item.get("link") or "",
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
                        "link": it.get("hotel_link") or "",
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
        # 局部修改模式：输入含 blocks + instruction，只改选中块
        if isinstance(data, dict) and data.get("blocks") is not None and data.get("instruction"):
            result = modify_blocks(
                data.get("blocks") or [],
                data.get("instruction") or "",
                data.get("search"),
                data.get("profile"),
                data.get("basic"),
            )
        else:
            if isinstance(data, dict) and any(k in data for k in ("search", "profile", "basic", "answers", "feedback", "modify")):
                search_result = data.get("search") or {}
                profile = data.get("profile")
                basic = data.get("basic")
                answers = data.get("answers")
                feedback = data.get("feedback")
                modify = data.get("modify")
            else:
                search_result = data
                profile = None
                basic = None
                answers = None
                feedback = None
                modify = None
            result = build_plan(search_result, profile, basic, answers, feedback, modify)
            if isinstance(result, dict) and "error" not in result:
                result["blocks"] = blockify(result)
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
