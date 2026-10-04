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

from route_map import attach_routes, geocode, geocode_blocks, strip_geo, _km

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
    "如果输入中包含 preferences（用户长期偏好，JSON），必须优先满足：餐饮口味/忌口、酒店硬性要求（含早/近地铁/预算等）、"
    "节奏偏好、交通方式、避雷点等都要落实；只有当 answers 明确与 preferences 冲突时，以本次 answers 为准。"
    "如果输入中包含 recent_trips（最近的历史行程），参考用户过去的选择：rating 低或 feedback 里提到的问题要避免；"
    "user_edits 中被用户改掉/删除的内容代表用户不想要，避免再次推荐同类安排。"
    "如果输入中包含 recent_trip_summary（对最近历史行程的语义摘要），优先依据摘要理解用户长期偏好和避雷点，"
    "recent_trips 原始记录作为补充。"
    "根据画像和旅行信息动态规划每日节奏与内容："
    "1) 景点数量：休闲度假/带老人→每天2个；深度文化/冒险户外/年轻单人→每天3个；其余2~3个；"
    "2) 景点形式按 travel_style 匹配：美食→餐饮街/老字号；亲子→乐园/动物园/海洋馆；文化→博物馆/古迹/艺术馆；"
    "自然→山水园林/湖景；购物→商圈；摄影→出片打卡点；冒险→徒步/户外体验；"
    "3) 美食：travel_style 含「美食探店」或 purposes 含「美食之旅」时，午晚餐各给具体饭店名；否则每餐1家即可；"
    "search.food 里的每条餐厅含 name/cuisine(菜系)/rating(评分)/price_per_person(人均)/business_area(商圈)/address，"
    "选餐厅时参考评分和人均是否符合预算，尽量选与当天景点同商圈的餐厅；",
    "4) 预算：经济档优先免费景点+公共交通，豪华档可付费体验+打车；总花费不超 total_budget；"
    "5) 人数与身份：带老人/孩子减少步行、安排休息点；学生控预算；退休轻松节奏；"
    "6) 交通：相邻景点在 note 里标注交通方式+大致耗时（打车/地铁/步行），尽量地理就近、少折返；"
    "若输入中包含 geo_hints（按地理片区聚类的地点提示），必须优先把同一片区的地点安排在同一天，"
    "且当天的 schedule 按片区内部的地理顺序排列；不要把同一片区的地点拆到不同天；"
    "7) 天气：雨天优先室内景点（博物馆/乐园室内馆），晴天可户外；"
    "8) 去程/回程：若 search 含 flights/trains，第一天 schedule 开头加一个 type=交通 的去程项"
    "（name 取 flights/trains 的 outbound 里的一项，note 写「出发地→目的地 出发时间 价格」），"
    "最后一天结尾加一个 type=交通 的回程项（name 取 flights/trains 的 inbound 里的一项，反向）；"
    "9) schedule 每天 4~6 项（景点+午晚餐+必要交通），每项 note ≤20 字；优先用 search 里的真实景点/酒店/饭店名称；"
    "9.5) 整个方案中同一个具体景点/活动名称只能出现一次，禁止「西湖」等景点在多个日期重复出现；"
    "每天优先选择 search.poi 中不同名称、不同 geo_hints 片区的地点，5 天尽量覆盖 5 个不同片区。"
    "如果输入中包含 day_assignments（每天应使用的景点片区），每天只能从对应 names 中选择景点，"
    "且不同天不得重复；同一片区若被分配多天，各天使用该片区内的不同景点。"
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

MODIFY_PLAN_PROMPT = (
    "你是旅行计划修改助手。根据用户指令修改完整的旅行计划。"
    "输入 plan（完整计划，含 destination/start_date/end_date/days/weather_summary/plans，"
    "每个 plan 含 style/summary/itinerary，itinerary 每天含 schedule）和 modify（修改指令）。"
    "modify 含 mode 和 instruction："
    "1) mode='global'：全局修改。按 instruction 整体调整计划（例如「行程太紧了」→减少每天景点数量、"
    "放慢节奏、增加休息或自由活动时间；「预算太高」→换成更便宜的选择）；"
    "2) mode='block'：block 修改。只修改或删除 modify.targets 指定的那些活动块"
    "（targets 是列表，每项含 name/type/day，例如「我不想去这里」→删除 target 对应的块，"
    "并合理顺延或填补后续安排），其余块尽量保持不变。"
    "输出修改后的完整 plan JSON，保持原结构（destination/start_date/end_date/days/weather_summary/plans/...）。"
    "每个 schedule 项含 time/type/name/note/link，type 取值 交通/美食/景点/酒店/活动。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

BLOCK_MODIFY_PROMPT = (
    "你是旅行计划修改助手。根据 instruction 修改某几天的行程。"
    "输入 days（需要修改的几天 itinerary，每天含 day/date/theme/hotel/schedule）和 modify"
    "（targets=要操作的块列表，每项含 name/type/day；instruction=指令）。"
    "只修改或删除 targets 对应的块，必要时顺延当天后续块的时间或填补空档；未涉及的块和天保持不变。"
    "输出 JSON：{\"days\":[修改后的 day itinerary...]}，每天结构保持 day/date/theme/hotel/schedule。"
    "schedule 项含 time/type/name/note/link。只输出 JSON，不要任何多余文字或代码块。"
)

TRIP_SUMMARY_PROMPT = (
    "你是用户旅行记忆摘要助手。根据给定的历史行程记录（recent_trips），"
    "提炼出对后续旅行规划有用的稳定偏好和教训。"
    "重点关注：用户选择的方案风格、评分与反馈（rating/feedback）、用户主动修改（user_edits）所透露的喜好与避雷点。"
    "输出一段不超过 150 字的摘要，用「偏好：...；避雷：...；节奏：...」的简洁形式。"
    "只输出摘要文字，不要 JSON、不要解释。"
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

    # events 仍按网页搜索结果处理（title + content）
    if isinstance(search.get("events"), list):
        result["events"] = [
            {"title": x.get("title"), "content": (x.get("content") or "")[:120]}
            for x in search["events"]
        ]

    # food：区分高德结构化数据和 Tavily 网页数据
    if isinstance(search.get("food"), list):
        trimmed_food = []
        for x in search["food"]:
            if x.get("_source") == "amap" or "cuisine" in x:
                # 高德结构化餐厅：保留规划需要的核心字段
                trimmed_food.append({
                    "name": x.get("name"),
                    "cuisine": x.get("cuisine"),
                    "rating": x.get("rating"),
                    "price_per_person": x.get("price_per_person"),
                    "business_area": x.get("business_area"),
                    "address": x.get("address"),
                })
            else:
                # Tavily 回退数据：title + content
                trimmed_food.append({
                    "title": x.get("title"),
                    "content": (x.get("content") or "")[:120],
                })
        result["food"] = trimmed_food

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


def _names_from_search(search: dict) -> list[str]:
    """收集 search 里所有可定位的地点名称，去重后返回。"""
    seen: list[str] = []
    for key in ("poi", "hotels", "food", "events", "promotions"):
        items = search.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            name = (item.get("name") or item.get("title") or "").strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def _geo_hints(search: dict, destination: str, cluster_km: float = 2.5) -> dict:
    """A 方案：给 PlanAgent 注入地理片区聚类提示。

    对 search 里的地点做高德地理编码，按直线距离聚成片区。
    没有 AMAP_KEY 或地理编码失败时返回空字典。
    """
    if not os.getenv("AMAP_KEY") or not destination:
        return {}
    names = _names_from_search(search)
    if not names:
        return {}

    located: list[tuple[str, str]] = []
    for name in names:
        hit = geocode(name, destination)
        if hit and hit.get("location"):
            located.append((name, hit["location"]))

    clusters: list[dict] = []
    for name, loc in located:
        placed = False
        for cluster in clusters:
            if _km(cluster["anchor"], loc) <= cluster_km:
                cluster["names"].append(name)
                placed = True
                break
        if not placed:
            clusters.append({"anchor": loc, "names": [name]})

    clusters = [c for c in clusters if len(c["names"]) > 1]
    if not clusters:
        return {}
    return {
        "hint": "以下地点地理上相邻，应安排在同一天并尽量连续游览，不要拆到不同天。",
        "clusters": [
            {"names": c["names"]}
            for c in sorted(clusters, key=lambda c: -len(c["names"]))
        ],
    }


def _chunks(items: list[str], count: int) -> list[list[str]]:
    count = max(1, min(count, len(items)))
    buckets: list[list[str]] = [[] for _ in range(count)]
    for index, item in enumerate(items):
        buckets[index % count].append(item)
    return [bucket for bucket in buckets if bucket]


def _assign_clusters(search: dict, destination: str, days: int) -> list[dict]:
    """把地理片区分配到每一天：每天不同片区，大片区可拆成多天。"""
    hints = _geo_hints(search, destination)
    clusters = hints.get("clusters") or []
    if not clusters or days <= 0:
        return []

    clusters = sorted(clusters, key=lambda c: -len(c["names"]))
    total = sum(len(c["names"]) for c in clusters)
    if total <= 0:
        return []

    raw_days = [max(1, round(len(c["names"]) / total * days)) for c in clusters]
    diff = days - sum(raw_days)
    order = sorted(range(len(clusters)), key=lambda i: -len(clusters[i]["names"]))

    while diff > 0:
        raw_days[order[0]] += 1
        diff -= 1
    while diff < 0:
        adjusted = False
        for i in order:
            if raw_days[i] > 1:
                raw_days[i] -= 1
                diff += 1
                adjusted = True
                break
        if not adjusted:
            break

    assignments: list[dict] = []
    day = 1
    for cluster, count in zip(clusters, raw_days):
        for chunk in _chunks(cluster["names"], count):
            assignments.append({"day": day, "names": chunk})
            day += 1
    return assignments[:days]


def _locate_schedule(plans: list[dict], destination: str) -> dict[str, str | None]:
    """给所有 schedule 活动块做地理编码，返回 {name: "lng,lat"}（失败为 None）。"""
    names: list[str] = []
    for p in plans:
        for it in p.get("itinerary") or []:
            for s in it.get("schedule") or []:
                name = (s.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
    locations: dict[str, str | None] = {}
    for name in names:
        hit = geocode(name, destination)
        locations[name] = (hit or {}).get("location") or None
    return locations


def _reorder_by_proximity(plan: dict, destination: str, move_km: float = 2.0) -> dict:
    """B 方案：生成后兜底，把明显相邻却分在不同天的非酒店活动合并到更近的那天。

    酒店 block 属于当天住宿，不跨天移动；交通（去程/回程）也不动；景点/美食/活动参与就近合并。
    只在能拿到坐标时生效，否则原样返回。
    """
    if not os.getenv("AMAP_KEY") or not destination:
        return plan
    plans = plan.get("plans") or []
    if not plans:
        return plan

    locations = _locate_schedule(plans, destination)
    movable_types = {"景点", "美食", "活动"}

    changed = False
    for p in plans:
        days = p.get("itinerary") or []
        day_blocks: list[list[dict]] = []
        for it in days:
            day_blocks.append(
                [
                    s for s in (it.get("schedule") or [])
                    if (s.get("type") in movable_types or not s.get("type"))
                ]
            )

        day_centers: list[tuple[float, float] | None] = []
        for movable in day_blocks:
            coords = []
            for s in movable:
                loc = locations.get((s.get("name") or "").strip())
                if loc:
                    coords.append(tuple(map(float, loc.split(","))))
            if coords:
                day_centers.append(
                    (
                        sum(c[0] for c in coords) / len(coords),
                        sum(c[1] for c in coords) / len(coords),
                    )
                )
            else:
                day_centers.append(None)

        new_day_blocks: list[list[dict]] = [[] for _ in days]
        for idx, movable in enumerate(day_blocks):
            for s in movable:
                loc = locations.get((s.get("name") or "").strip())
                if not loc:
                    new_day_blocks[idx].append(s)
                    continue
                target = idx
                best_gain = 0.0
                for prev in range(idx):
                    center = day_centers[prev]
                    if not center:
                        continue
                    d_prev = _km(f"{center[0]},{center[1]}", loc)
                    own = day_centers[idx]
                    d_own = _km(f"{own[0]},{own[1]}", loc) if own else 999.0
                    gain = d_own - d_prev
                    if d_prev <= move_km and gain > best_gain:
                        best_gain = gain
                        target = prev
                if target != idx:
                    changed = True
                new_day_blocks[target].append(s)

        for it, movable in zip(days, new_day_blocks):
            fixed = [
                s for s in (it.get("schedule") or [])
                if s.get("type") not in movable_types and s.get("type")
            ]
            # 合并后按 time 重排，避免把被移动的活动插到错误的时间顺序里
            merged = fixed + movable
            merged.sort(
                key=lambda s: (s.get("time") or "99:99")
            )
            it["schedule"] = merged

    return plan if changed else plan


def _dedupe_poi_names(plan: dict, search_result: dict) -> dict:
    """兜底去重：同一个景点名如果被排到多个日期，用 search.poi 里的未用景点替换后续重复项。"""
    poi_names = [
        (p.get("name") or "").strip()
        for p in (search_result.get("poi") or [])
        if (p.get("name") or "").strip()
    ]
    unused = [name for name in poi_names]

    for p in plan.get("plans") or []:
        seen: set[str] = set()
        for it in p.get("itinerary") or []:
            for s in it.get("schedule") or []:
                if s.get("type") != "景点":
                    continue
                name = (s.get("name") or "").strip()
                if not name:
                    continue
                if name in seen:
                    replacement = next(
                        (candidate for candidate in unused if candidate not in seen),
                        None,
                    )
                    if replacement:
                        s["name"] = replacement
                        s["note"] = (s.get("note") or "")[:0]
                        seen.add(replacement)
                        unused.remove(replacement)
                    continue
                seen.add(name)
                if name in unused:
                    unused.remove(name)
    return plan


def _backfill_links(plan: dict, search: dict) -> dict:
    """生成后按名称匹配回填链接，避免 url 进 prompt 导致 prompt 过长。"""
    entries: list[tuple[str, str]] = []
    for key in ("hotels", "poi", "food", "events", "promotions"):
        items = search.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            name = item.get("name") or item.get("title") or ""
            # 高德餐厅优先用 POI 详情链接，其次地图标记链接，最后通用 url
            url = (
                item.get("poi_detail_url")
                or item.get("map_url")
                or item.get("url")
                or ""
            )
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


def _summarize_trips(client: OpenAI, recent_trips: list) -> str:
    """把最近的历史行程做一次语义摘要，提炼稳定偏好和避雷点。"""
    if not recent_trips:
        return ""
    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
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
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def build_plan(
    search_result: dict,
    profile: dict | None = None,
    preferences: dict | None = None,
    recent_trips: list | None = None,
    basic: dict | None = None,
    answers: list | None = None,
    feedback: str | None = None,
    modify: dict | None = None,
    recent_trip_summary: str | None = None,
    skip_trip_summary: bool = False,
) -> dict:
    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    meta = _build_meta(search_result)
    destination = search_result.get("destination") or ""
    days = meta.get("days") or 0

    context: dict = {"search": _trim_search(search_result)}
    geo_hints = _geo_hints(search_result, destination)
    if geo_hints:
        context["geo_hints"] = geo_hints
    day_assignments = _assign_clusters(search_result, destination, days)
    if day_assignments:
        context["day_assignments"] = day_assignments
    if profile:
        context["user_profile"] = profile
    if preferences:
        context["preferences"] = preferences
    if recent_trips:
        context["recent_trips"] = recent_trips
    computed_summary = ""
    if recent_trip_summary:
        context["recent_trip_summary"] = recent_trip_summary
    elif recent_trips and not skip_trip_summary:
        computed_summary = _summarize_trips(client, recent_trips)
        if computed_summary:
            context["recent_trip_summary"] = computed_summary
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

    result = meta
    result["plans"] = plans
    result = _dedupe_poi_names(result, search_result)
    result = _reorder_by_proximity(result, result.get("destination") or "")
    if computed_summary:
        result["recent_trip_summary"] = computed_summary
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


def _modify_block_local(
    plan: dict,
    modify: dict,
    search_result: dict | None = None,
    profile: dict | None = None,
    basic: dict | None = None,
) -> dict:
    """block 修改：只处理受影响的 day，LLM 局部输出（快），再合并回原 plan。"""
    block_map = {b.get("id"): b for b in (plan.get("blocks") or [])}
    affected: set[tuple] = set()
    for bid in modify.get("block_ids") or []:
        b = block_map.get(bid) or {}
        if b.get("plan_style") and b.get("day") is not None:
            affected.add((b.get("plan_style"), b.get("day")))

    if not affected:
        return plan

    days_to_modify: list[dict] = []
    for p in plan.get("plans") or []:
        style = p.get("style")
        for it in p.get("itinerary") or []:
            if (style, it.get("day")) in affected:
                days_to_modify.append(it)

    targets = []
    for bid in modify.get("block_ids") or []:
        b = block_map.get(bid) or {}
        targets.append(
            {"name": b.get("name"), "type": b.get("type"), "day": b.get("day")}
        )

    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)
    context: dict = {
        "days": days_to_modify,
        "modify": {"targets": targets, "instruction": modify.get("instruction", "")},
    }
    if search_result:
        context["search"] = _trim_search(search_result)
    if profile:
        context["user_profile"] = profile
    if basic:
        context["basic"] = basic

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": BLOCK_MODIFY_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        max_tokens=4000,
        timeout=120,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    modified_days = json.loads(content).get("days") or []

    # 按顺序合并回 plan
    modified_idx = 0
    for p in plan.get("plans") or []:
        style = p.get("style")
        new_itinerary = []
        for it in p.get("itinerary") or []:
            if (style, it.get("day")) in affected:
                if modified_idx < len(modified_days):
                    new_itinerary.append(modified_days[modified_idx])
                    modified_idx += 1
                else:
                    new_itinerary.append(it)
            else:
                new_itinerary.append(it)
        p["itinerary"] = new_itinerary
    return plan


def modify_plan(
    plan: dict,
    modify: dict,
    search_result: dict | None = None,
    profile: dict | None = None,
    basic: dict | None = None,
) -> dict:
    """基于上一版完整计划做修改，支持全局修改（global）和 block 修改（block）。"""
    if (modify or {}).get("mode") == "block":
        return _modify_block_local(plan, modify, search_result, profile, basic)

    kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)

    # 把 block_ids 转成要操作的块内容（名称/类型/天），方便 LLM 定位
    modify = dict(modify or {})
    if modify.get("mode") == "block" and modify.get("block_ids"):
        block_map = {b.get("id"): b for b in (plan.get("blocks") or [])}
        targets = []
        for bid in modify["block_ids"]:
            b = block_map.get(bid) or {}
            targets.append(
                {"name": b.get("name"), "type": b.get("type"), "day": b.get("day")}
            )
        modify["targets"] = targets

    # 传给 LLM 的 plan 只保留嵌套 plans，去掉扁平 blocks，避免结构混淆
    llm_plan = {k: v for k, v in plan.items() if k != "blocks"}

    context: dict = {"plan": llm_plan, "modify": modify}
    if search_result:
        context["search"] = _trim_search(search_result)
    if profile:
        context["user_profile"] = profile
    if basic:
        context["basic"] = basic

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": MODIFY_PLAN_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
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
        # 完整计划修改：输入含 plan + modify（支持全局修改 / block 修改）
        if isinstance(data, dict) and data.get("plan") is not None and data.get("modify"):
            result = modify_plan(
                data.get("plan") or {},
                data.get("modify") or {},
                data.get("search"),
                data.get("profile"),
                data.get("basic"),
            )
            if isinstance(result, dict) and "error" not in result:
                result["blocks"] = blockify(result)
        # 局部修改模式：输入含 blocks + instruction，只改选中块
        elif isinstance(data, dict) and data.get("blocks") is not None and data.get("instruction"):
            result = modify_blocks(
                data.get("blocks") or [],
                data.get("instruction") or "",
                data.get("search"),
                data.get("profile"),
                data.get("basic"),
            )
            # 改过的块重新定位，前端据此重画路线
            if isinstance(result, dict) and isinstance(result.get("blocks"), list):
                geocode_blocks(result["blocks"], (data.get("search") or {}).get("destination") or "")
                strip_geo(result["blocks"])
        else:
            if isinstance(data, dict) and any(
                k in data
                for k in (
                    "search",
                    "profile",
                    "preferences",
                    "recent_trips",
                    "recent_trip_summary",
                    "skip_trip_summary",
                    "basic",
                    "answers",
                    "feedback",
                    "modify",
                )
            ):
                search_result = data.get("search") or {}
                profile = data.get("profile")
                preferences = data.get("preferences")
                recent_trips = data.get("recent_trips")
                recent_trip_summary = data.get("recent_trip_summary")
                skip_trip_summary = bool(data.get("skip_trip_summary"))
                basic = data.get("basic")
                answers = data.get("answers")
                feedback = data.get("feedback")
                modify = data.get("modify")
            else:
                search_result = data
                profile = None
                preferences = None
                recent_trips = None
                recent_trip_summary = None
                skip_trip_summary = False
                basic = None
                answers = None
                feedback = None
                modify = None
            result = build_plan(
                search_result,
                profile,
                preferences,
                recent_trips,
                basic,
                answers,
                feedback,
                modify,
                recent_trip_summary,
                skip_trip_summary,
            )
            if isinstance(result, dict) and "error" not in result:
                result["blocks"] = blockify(result)
                attach_routes(result, result.get("destination") or "")
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
