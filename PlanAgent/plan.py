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
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from route_map import attach_routes, geocode, geocode_blocks, strip_geo, _km

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
ROOT = BASE_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_env import component_python, component_script, subprocess_env  # noqa: E402

SEARCH_PY = component_script("SearchAgent", "search.py")
SEARCH_PYTHON = component_python("SearchAgent")

DAY_PLAN_PROMPT = (
    "你是旅行规划师，负责规划某一天的行程。"
    "输入包含 day（第几天）、date、block（当天分配的景点片区，names 是可选的景点名）、"
    "search（酒店/机票/高铁等）、user_profile、preferences、basic、answers 等。"
    "规则：根据 user_profile.travel_style 或 preferences.pace 调整节奏："
    "休闲度假/轻松→每天 1~2 个景点，慢节奏、安排休息；摄影旅拍/打卡/紧凑→每天 3 个景点，优先网红出片点，行程充实。"
    "如果是第一天且去程航班/高铁到达时间较晚（15:00 以后），可只安排 1 个景点或仅安排晚餐/入住；"
    "如果到达时间晚于 20:00，第一天不要再安排景点，只安排交通和入住；禁止在 00:00 之后安排景点。"
    "其他情况当天至少安排 2 个景点；若 block.names 少于 2 个，可从 search.poi 里选地理相邻的景点补充。"
    "根据景点 scale/duration 安排数量与时间：大景点(全天)每天 1 个，中景点每天 2 个，小景点每天最多 3 个；"
    "不要在同一天塞入两个全天型大景点。"
    "优先选择榜单名次靠前（rank 小）的景点；如果当天 block 内有全天型高热度景点，优先把它安排进去，"
    "其他可顺延的小景点放到别的天。"
    "按时间与距离合理安排：当天所有景点的 duration 累加（含交通）控制在 8 小时以内；"
    "优先把同区县、同 geo_hints 片区的景点排在一起，相邻景点交通耗时尽量不超过 30 分钟；"
    "跨区且交通超过 1 小时的景点不要硬塞在同一天。"
    "当天景点必须按地理顺序排成一条不折返的路线：如果 C 离 A 很近，就应安排 A→C→B，而不是 A→B→C。"
    "最后一天若回程时间在傍晚或晚上，不要安排距离车站/机场超过 1 小时的远郊景点。"
    "schedule 必须按时间从早到晚排列。"
    "当天只从 block.names 里选景点；"
    "本阶段不要安排美食/餐饮，餐饮稍后会单独补充。"
    "如果是第一天且 search 有 flights/trains 去程，开头加一个 type=交通 的去程项；"
    "如果是最后一天且 search 有 flights/trains 回程，结尾加一个 type=交通 的回程项；"
    "推荐一家当天酒店（search.hotels）。"
    "酒店尽量选在当天最后一个景点附近，或第二天第一个景点附近；远郊游玩当天不要安排住回市区。"
    "如果是最后一天且当晚已安排回程交通，酒店字段填「当晚返程，无住宿」。"
    "输出 JSON：{\"day\":N,\"date\":\"YYYY-MM-DD\",\"theme\":\"当天主题\",\"hotel\":\"酒店名\","
    "\"schedule\":[{\"time\":\"09:00-11:00\",\"type\":\"景点\",\"name\":\"活动名\",\"note\":\"简短说明\"}]}。"
    "只输出 JSON，不要任何多余文字或代码块。"
)


CLASSIFY_MODIFY_PROMPT = (
    "你是旅行计划修改意图分类器。根据用户指令和现有行程块，判断修改方式。"
    "如果指令指向某个具体景点/酒店/活动（例如「我不想去雷峰塔」「把酒店换成豪华型」），"
    "mode=block，并在 targets 中列出涉及的块（每项含 name/type/day）；"
    "如果是整体意见（例如「行程太紧」「预算太高」「节奏慢一点」），mode=global，targets=[]。"
    "输出 JSON：{\"mode\":\"global或block\",\"targets\":[{\"name\":\"...\",\"type\":\"...\",\"day\":1}]}。"
    "只输出 JSON，不要任何多余文字。"
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
                "district": p.get("district_label") or p.get("district") or "",
                "duration": p.get("duration") or "",
                "scale": p.get("scale") or "",
                "longitude": p.get("longitude"),
                "latitude": p.get("latitude"),
                "description": (p.get("description") or "")[:80],
            }
            for p in search["poi"]
        ]

    if isinstance(search.get("hotels"), list):
        result["hotels"] = [
            {
                "name": h.get("name"),
                "star": h.get("star"),
                "price": h.get("price"),
                "location": h.get("location"),
                "longitude": h.get("longitude"),
                "latitude": h.get("latitude"),
            }
            for h in search["hotels"]
        ]

    # food：第二阶段补入计划，仍保留结构化餐厅数据
    if isinstance(search.get("food"), list):
        result["food"] = [
            {
                "name": x.get("name"),
                "cuisine": x.get("cuisine"),
                "rating": x.get("rating"),
                "price_per_person": x.get("price_per_person"),
                "business_area": x.get("business_area"),
                "address": x.get("address"),
                "longitude": x.get("longitude"),
                "latitude": x.get("latitude"),
            }
            for x in search["food"]
            if x.get("name")
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


def _short_district(value: str | None) -> str:
    """从完整地址里提取区县级行政区，用于分 block 时兜底。"""
    value = (value or "").strip()
    if not value:
        return ""
    candidates: list[tuple[int, str]] = []
    for suffix in ("自治县", "区", "县", "旗"):
        start = 0
        while True:
            idx = value.find(suffix, start)
            if idx == -1:
                break
            if suffix == "区" and idx >= 2 and value[idx - 2:idx] == "自治":
                start = idx + 1
                continue
            candidates.append((idx, suffix))
            break
    if not candidates:
        return ""
    pos, suffix = min(candidates, key=lambda item: item[0])
    prefix = value[:pos]
    cut = -1
    for sep in ("自治州", "自治区", "市", "省"):
        idx = prefix.rfind(sep)
        if idx != -1:
            cut = max(cut, idx + len(sep))
    name = prefix[cut:] if cut != -1 else prefix
    return f"{name}{suffix}"


def _ensure_requested_pois(
    search_result: dict,
    destination: str,
    requested_pois: list[str],
) -> dict:
    """补搜用户明确要求但 SearchAgent 结果里没有的地点。"""
    existing = {
        (p.get("name") or "").strip()
        for p in (search_result.get("poi") or [])
        if (p.get("name") or "").strip()
    }
    poi_list = list(search_result.get("poi") or [])
    for name in requested_pois:
        name = (name or "").strip()
        if not name or name in existing:
            continue
        try:
            proc = subprocess.run(
                [
                    str(SEARCH_PYTHON),
                    str(SEARCH_PY),
                    "--poi-keyword",
                    name,
                    "--city",
                    destination,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                env=subprocess_env(),
            )
            extra = json.loads(proc.stdout).get("poi") or []
        except Exception:
            extra = []
        for item in extra:
            item_name = (item.get("name") or "").strip()
            if item_name and item_name not in existing:
                existing.add(item_name)
                item["source"] = "on_demand"
                poi_list.append(item)
    search_result["poi"] = poi_list
    return search_result


def _rank_score(rank: str | None) -> int:
    """从飞猪榜单文案里解析名次，用于 block 内热度排序。"""
    import re as _re

    text = str(rank or "")
    match = _re.search(r"第\s*(\d+)\s*名", text)
    return int(match.group(1)) if match else 0


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


def _build_blocks(
    search: dict,
    destination: str,
    cluster_km: float = 2.5,
    max_km: float = 60.0,
) -> list[dict]:
    """按区县标签 + 高德坐标聚类，把景点分成一个个地理 block。"""
    pois = search.get("poi") or []
    items: list[dict] = []
    for p in pois:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        hit = geocode(name, destination)
        loc = (hit or {}).get("location") or ""
        district = (
            p.get("district_label")
            or _short_district(p.get("district") or "")
            or ""
        )
        items.append(
            {
                "name": name,
                "district": district,
                "loc": loc,
                "score": _rank_score(p.get("rank") or ""),
            }
        )

    # 以城市中心为基准，先过滤掉过远的景点（如千岛湖之于杭州主城）
    city_hit = (
        geocode(destination, destination)
        or geocode(f"{destination}市", destination)
        or geocode(destination, "")
    )
    city_loc = (city_hit or {}).get("location") or ""
    if city_loc:
        kept: list[dict] = []
        for item in items:
            if not item["loc"]:
                kept.append(item)
                continue
            if _km(city_loc, item["loc"]) <= max_km:
                kept.append(item)
        if kept:
            items = kept

    by_district: dict[str, list[dict]] = {}
    for item in items:
        key = item["district"] or "__none__"
        by_district.setdefault(key, []).append(item)

    blocks: list[dict] = []
    for district, members in by_district.items():
        if district == "__none__":
            continue
        members.sort(key=lambda m: (m.get("score", 0) == 0, m.get("score", 0)))
        names = [m["name"] for m in members]
        scores = [m.get("score", 0) for m in members]
        locs = [m["loc"] for m in members if m["loc"]]
        center = None
        if locs:
            lngs = [float(loc.split(",")[0]) for loc in locs]
            lats = [float(loc.split(",")[1]) for loc in locs]
            center = [sum(lngs) / len(lngs), sum(lats) / len(lats)]
        block_id = f"{district}-1"
        blocks.append(
            {
                "block_id": block_id,
                "district": "" if district == "__none__" else district,
                "names": names,
                "scores": scores,
                "center": center,
            }
        )

    # 没有标准区县标签的景点，就近并到已有 block
    for item in by_district.get("__none__", []):
        if not blocks:
            blocks.append(
                {
                    "block_id": "block-1",
                    "district": "",
                    "names": [item["name"]],
                    "scores": [item.get("score", 0)],
                    "center": None,
                }
            )
            continue
        candidates = [i for i, b in enumerate(blocks) if b.get("center")]
        if candidates and item["loc"]:
            target = min(
                candidates,
                key=lambda i: _km(
                    f"{blocks[i]['center'][0]},{blocks[i]['center'][1]}",
                    item["loc"],
                ),
            )
        else:
            target = max(
                range(len(blocks)),
                key=lambda i: len(blocks[i].get("names") or []),
            )
        blocks[target]["names"].append(item["name"])
        blocks[target]["scores"].append(item.get("score", 0))
        if blocks[target].get("scores"):
            pairs = sorted(
                zip(blocks[target]["scores"], blocks[target]["names"]),
                key=lambda pair: -pair[0],
            )
            blocks[target]["scores"] = [s for s, _ in pairs]
            blocks[target]["names"] = [n for _, n in pairs]
        if item["loc"] and not blocks[target].get("center"):
            lng, lat = item["loc"].split(",")
            blocks[target]["center"] = [float(lng), float(lat)]

    return blocks


def _merge_small_blocks(blocks: list[dict], min_size: int = 3) -> list[dict]:
    """把小 block 合并到最近的 block，避免出现大量单点 block。"""
    working = [dict(b) for b in blocks]
    while True:
        small = [i for i, b in enumerate(working) if len(b.get("names") or []) < min_size]
        if not small:
            break
        index = small[0]
        block = working[index]
        candidates = [j for j, b in enumerate(working) if j != index and b.get("center")]
        if candidates:
            target = min(
                candidates,
                key=lambda j: _km(
                    f"{working[j]['center'][0]},{working[j]['center'][1]}",
                    f"{block['center'][0]},{block['center'][1]}",
                )
                if block.get("center")
                else 0,
            )
            if block.get("center") is None:
                target = max(candidates, key=lambda j: len(working[j].get("names") or []))
        else:
            if len(working) <= 1:
                break
            target = max(
                [j for j in range(len(working)) if j != index],
                key=lambda j: len(working[j].get("names") or []),
            )
        working[target]["names"] = (working[target].get("names") or []) + (block.get("names") or [])
        working[target]["scores"] = (working[target].get("scores") or []) + (block.get("scores") or [])
        if working[target].get("scores"):
            pairs = sorted(
                zip(working[target]["scores"], working[target]["names"]),
                key=lambda pair: -pair[0],
            )
            working[target]["scores"] = [s for s, _ in pairs]
            working[target]["names"] = [n for _, n in pairs]
        working[target]["district"] = working[target].get("district") or block.get("district") or ""
        working.pop(index)
    return working


def _split_block(block: dict, count: int) -> list[dict]:
    parts = _chunks(block.get("names") or [], count)
    score_parts = _chunks(block.get("scores") or [], count)
    return [
        {
            "block_id": f"{block['block_id']}-{i + 1}",
            "district": block.get("district") or "",
            "names": part,
            "scores": score_parts[i] if i < len(score_parts) else [],
            "center": block.get("center"),
        }
        for i, part in enumerate(parts)
    ]


def _first_day_arrives_late(search_result: dict) -> bool:
    """判断去程航班/高铁是否在 15:00 之后到达。"""
    for key in ("flights", "trains"):
        items = search_result.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("direction") != "去":
                continue
            match = re.search(r"(\d{1,2}):\d{2}", str(item.get("arr_time") or ""))
            if match and int(match.group(1)) >= 15:
                return True
    return False


def _score_blocks(
    blocks: list[dict],
    city_center: list[float] | None,
    profile: dict | None = None,
) -> list[dict]:
    """给每个区打分：热度 + 距离 + 密度 + 匹配 + 交通。"""
    scored: list[tuple[float, dict]] = []
    for block in blocks:
        scores = [s for s in (block.get("scores") or []) if s and s > 0]
        hot = sum(1.0 / s for s in scores) / len(scores) if scores else 0.0

        if city_center and block.get("center"):
            dist = _km(
                f"{city_center[0]},{city_center[1]}",
                f"{block['center'][0]},{block['center'][1]}",
            )
            distance_score = 1.0 / (1.0 + dist / 10.0)
        else:
            distance_score = 0.5

        density = min(1.0, len(block.get("names") or []) / 8.0)
        match = 0.5
        transport = 0.5

        total = (
            0.30 * hot
            + 0.25 * distance_score
            + 0.20 * density
            + 0.15 * match
            + 0.10 * transport
        )
        scored.append((total, block))

    scored.sort(key=lambda pair: -pair[0])
    return [block for _, block in scored]


def _assign_blocks_to_days(
    blocks: list[dict],
    days: int,
    first_day_late: bool = False,
    city_center: list[float] | None = None,
) -> list[dict]:
    """固定 1 block/天：把 block 数量调整到 days，每个 block 对应一天。"""
    if not blocks or days <= 0:
        return []
    working = [dict(b) for b in blocks]

    # block 数量少于天数时，拆最大的 block
    while len(working) < days:
        working.sort(key=lambda b: -len(b.get("names") or []))
        largest = working[0]
        if len(largest.get("names") or []) < 2:
            break
        working = working[1:] + _split_block(largest, 2)

    # 仍少于天数时，补空 block（按区县/景点兜底）
    while len(working) < days:
        working.append({"block_id": f"empty-{len(working) + 1}", "district": "", "names": [], "center": None})

    # block 数量多于天数时，合并最近的 block
    while len(working) > days:
        located = [b for b in working if b.get("center")]
        if city_center:
            global_center = city_center
        elif located:
            total_w = sum(len(b.get("names") or []) for b in located) or 1
            global_center = [
                sum((b["center"][0] * len(b.get("names") or [])) for b in located) / total_w,
                sum((b["center"][1] * len(b.get("names") or [])) for b in located) / total_w,
            ]
        else:
            global_center = None

        working = _score_blocks(working, global_center)
        working = working[:days]

        # 按地理就近串成一天接一天的顺序，避免第一天和最后一天跨区跳远
        if len(working) > 1 and all(b.get("center") for b in working):
            ordered = [working[0]]
            remaining = working[1:]
            while remaining:
                last = ordered[-1]
                nxt = min(
                    remaining,
                    key=lambda b: _km(
                        f"{last['center'][0]},{last['center'][1]}",
                        f"{b['center'][0]},{b['center'][1]}",
                    ),
                )
                ordered.append(nxt)
                remaining.remove(nxt)
            working = ordered

        # 最后一天尽量回到城市中心附近，方便返程
        if len(working) >= 3 and global_center:
            def distance_to_center(block: dict) -> float:
                if not block.get("center"):
                    return float("inf")
                return _km(
                    f"{block['center'][0]},{block['center'][1]}",
                    f"{global_center[0]},{global_center[1]}",
                )

            first = working[0]
            rest = working[1:]
            last_day = min(rest, key=distance_to_center)
            middle = [b for b in rest if b is not last_day]
            working = [first, *middle, last_day]
        break

    assignments = []
    for i, block in enumerate(working):
        assignments.append(
            {
                "day": i + 1,
                "block_id": block.get("block_id"),
                "district": block.get("district"),
                "names": block.get("names") or [],
                "center": block.get("center"),
            }
        )
    if first_day_late and len(assignments) > 1:
        assignments[0], assignments[1] = assignments[1], assignments[0]
    for index, assignment in enumerate(assignments):
        assignment["day"] = index + 1
    return assignments


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


def _build_day_plan(
    client: OpenAI,
    context: dict,
    day_assignment: dict,
    total_days: int,
    start_date: str,
) -> dict:
    """按单个地理 block 生成一天的行程。"""
    day = int(day_assignment.get("day") or 0)
    names = day_assignment.get("names") or []

    ctx = dict(context)
    search = dict(ctx.get("search") or {})
    if names and isinstance(search.get("poi"), list):
        name_set = set(names)
        search["poi"] = [p for p in search["poi"] if (p.get("name") or "") in name_set]
    ctx["search"] = search
    ctx["day"] = day
    ctx["block"] = {
        "block_id": day_assignment.get("block_id"),
        "district": day_assignment.get("district"),
        "names": names,
    }
    ctx["is_first_day"] = day == 1
    ctx["is_last_day"] = day == total_days
    try:
        ctx["date"] = (date.fromisoformat(start_date) + timedelta(days=day - 1)).isoformat()
    except ValueError:
        ctx["date"] = start_date

    last: dict = {}
    for _ in range(2):
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": DAY_PLAN_PROMPT},
                {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
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
        try:
            last = json.loads(content)
        except json.JSONDecodeError:
            last = {}
        if isinstance(last, dict) and last.get("schedule"):
            if isinstance(last.get("schedule"), list):
                last["schedule"].sort(key=lambda s: (s.get("time") or "99:99"))
            return last
    if not isinstance(last, dict):
        last = {}
    last.setdefault("day", day)
    last.setdefault("date", ctx.get("date") or "")
    last.setdefault("theme", "")
    last.setdefault("hotel", "")
    last.setdefault("schedule", [])
    if not last["schedule"] and names:
        times = ["09:00-11:00", "14:00-16:00"]
        for index, name in enumerate(names[:2]):
            last["schedule"].append(
                {
                    "time": times[index],
                    "type": "景点",
                    "name": name,
                    "note": "",
                }
            )
    return last


def _pick_restaurant(
    food: list[dict],
    district: str,
    used: set[str],
    center: list[float] | None,
) -> dict | None:
    candidates = [
        f for f in food
        if (f.get("name") or "").strip() and (f.get("name") or "").strip() not in used
    ]
    if not candidates:
        return None
    if district:
        district_match = [
            f for f in candidates
            if district in str(f.get("business_area") or "")
            or district in str(f.get("address") or "")
        ]
        if district_match:
            candidates = district_match
    if center:
        def distance(f: dict) -> float:
            lng = f.get("longitude")
            lat = f.get("latitude")
            if lng is None or lat is None:
                return float("inf")
            return _km(
                f"{center[0]},{center[1]}",
                f"{float(lng)},{float(lat)}",
            )

        candidates.sort(
            key=lambda f: (
                distance(f),
                f.get("rating") is None,
                -(f.get("rating") or 0),
            )
        )
    else:
        candidates.sort(key=lambda f: (f.get("rating") is None, -(f.get("rating") or 0)))
    return candidates[0]


def _to_minutes(value: str) -> int | None:
    match = re.search(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _slot_time(schedule: list[dict], kind: str) -> str:
    """根据当天景点时间，找午餐/晚餐的空档时间。"""
    occupied: list[tuple[int, int]] = []
    for item in schedule:
        if (item.get("type") or "") not in ("景点", "活动"):
            continue
        match = re.match(
            r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})",
            str(item.get("time") or ""),
        )
        if match:
            start = _to_minutes(match.group(1))
            end = _to_minutes(match.group(2))
            if start is not None and end is not None:
                occupied.append((start, end))

    window = (11 * 60, 14 * 60) if kind == "午餐" else (17 * 60, 20 * 60)
    occupied = sorted(o for o in occupied if o[1] > window[0] and o[0] < window[1])

    free_from = window[0]
    best_start = window[0]
    best_len = 0
    for start, end in occupied:
        start = max(start, window[0])
        end = min(end, window[1])
        if start > free_from and (start - free_from) > best_len:
            best_len = start - free_from
            best_start = free_from
        free_from = max(free_from, end)
    if window[1] - free_from > best_len:
        best_len = window[1] - free_from
        best_start = free_from

    start = best_start if best_len >= 40 else window[0]
    end = min(start + 60, window[1])
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def _add_food_to_day(
    day: dict,
    food: list[dict],
    district: str,
    center: list[float] | None,
    poi_map: dict[str, list[float]],
    used_restaurants: set[str],
) -> dict:
    """第二阶段：给已生成的一天行程补午餐/晚餐，并跨天去重。"""
    schedule = list(day.get("schedule") or [])
    if any((s.get("type") or "") in ("美食", "餐饮") for s in schedule):
        return day

    # 当天有长时段大景点时，不插入外部餐厅
    for item in schedule:
        if (item.get("type") or "") != "景点":
            continue
        match = re.match(
            r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})",
            str(item.get("time") or ""),
        )
        if match:
            start = _to_minutes(match.group(1))
            end = _to_minutes(match.group(2))
            if start is not None and end is not None and (end - start) >= 360:
                return day

    for meal in ("午餐", "晚餐"):
        time = _slot_time(schedule, meal)
        anchor = center
        spots = [
            s for s in schedule
            if (s.get("type") or "") == "景点"
            and poi_map.get((s.get("name") or "").strip())
        ]
        if meal == "午餐":
            before_noon = [
                s for s in spots
                if (s.get("time") or "").startswith(("09", "10", "11"))
            ]
            if before_noon:
                anchor = poi_map[(before_noon[-1].get("name") or "").strip()]
        elif spots:
            anchor = poi_map[(spots[-1].get("name") or "").strip()]
        restaurant = _pick_restaurant(food, district, used_restaurants, anchor)
        if restaurant:
            schedule.append(
                {
                    "time": time,
                    "type": "美食",
                    "name": restaurant.get("name"),
                    "note": f"{meal}推荐",
                }
            )
            used_restaurants.add(str(restaurant.get("name") or ""))
    schedule.sort(key=lambda s: (s.get("time") or "99:99"))
    day["schedule"] = schedule
    return day


def _pick_hotel(
    hotels: list[dict],
    center: list[float] | None,
    used_hotels: set[str],
) -> dict | None:
    candidates = [
        h for h in hotels
        if (h.get("name") or "").strip() and (h.get("name") or "").strip() not in used_hotels
    ]
    if not candidates:
        return None
    if center:
        def distance(h: dict) -> float:
            lng = h.get("longitude")
            lat = h.get("latitude")
            if lng is None or lat is None:
                return float("inf")
            return _km(f"{center[0]},{center[1]}", f"{float(lng)},{float(lat)}")

        candidates.sort(key=distance)
    else:
        candidates.sort(key=lambda h: (h.get("star") is None, -(len(h.get("star") or ""))))
    return candidates[0]


def _add_hotel_to_day(
    day: dict,
    hotels: list[dict],
    center: list[float] | None,
    poi_map: dict[str, list[float]],
    used_hotels: set[str],
) -> dict:
    if (day.get("hotel") or "").strip():
        return day
    spots = [
        s for s in (day.get("schedule") or [])
        if (s.get("type") or "") == "景点"
        and poi_map.get((s.get("name") or "").strip())
    ]
    anchor = poi_map[(spots[-1].get("name") or "").strip()] if spots else center
    hotel = _pick_hotel(hotels, anchor, used_hotels)
    if hotel:
        day["hotel"] = hotel.get("name") or ""
        used_hotels.add(str(hotel.get("name") or ""))
    return day


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

    requested_pois = (basic or {}).get("requested_pois") or []
    if requested_pois:
        search_result = _ensure_requested_pois(search_result, destination, requested_pois)

    city_hit = (
        geocode(destination, destination)
        or geocode(f"{destination}市", destination)
        or geocode(destination, "")
    )
    city_center = None
    if city_hit and city_hit.get("location"):
        lng, lat = city_hit["location"].split(",")
        city_center = [float(lng), float(lat)]

    context: dict = {"search": _trim_search(search_result)}
    geo_hints = _geo_hints(search_result, destination)
    if geo_hints:
        context["geo_hints"] = geo_hints
    blocks = _build_blocks(search_result, destination)
    day_assignments = _assign_blocks_to_days(
        blocks,
        days,
        first_day_late=_first_day_arrives_late(search_result),
        city_center=city_center,
    )
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

    with ThreadPoolExecutor(max_workers=min(max(days, 1), 8)) as executor:
        day_plans = list(
            executor.map(
                lambda da: _build_day_plan(
                    client, context, da, days, meta.get("start_date") or ""
                ),
                day_assignments,
            )
        )
    day_plans = sorted(day_plans, key=lambda d: d.get("day") or 0)
    food = search_result.get("food") or []
    district_by_day = {
        a.get("day"): a.get("district") or ""
        for a in day_assignments
    }
    center_by_day = {
        a.get("day"): a.get("center")
        for a in day_assignments
    }
    poi_map: dict[str, list[float]] = {}
    for poi in search_result.get("poi") or []:
        name = (poi.get("name") or "").strip()
        lng = poi.get("longitude")
        lat = poi.get("latitude")
        if name and lng is not None and lat is not None:
            poi_map[name] = [float(lng), float(lat)]
    used_restaurants: set[str] = set()
    for day_plan in day_plans:
        _add_food_to_day(
            day_plan,
            food,
            district_by_day.get(day_plan.get("day"), ""),
            center_by_day.get(day_plan.get("day")),
            poi_map,
            used_restaurants,
        )
    hotels = search_result.get("hotels") or []
    used_hotels: set[str] = set()
    for day_plan in day_plans:
        day_num = day_plan.get("day")
        if day_num == days:
            if not (day_plan.get("hotel") or "").strip():
                day_plan["hotel"] = "当晚返程，无住宿"
        else:
            _add_hotel_to_day(
                day_plan,
                hotels,
                center_by_day.get(day_num),
                poi_map,
                used_hotels,
            )

    result = meta
    result["plans"] = [
        {
            "style": "推荐方案",
            "summary": f"{destination} {days} 日游",
            "itinerary": day_plans,
        }
    ]
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


def classify_modify(client: OpenAI, instruction: str, blocks: list[dict]) -> dict:
    brief = [
        {
            "id": b.get("id"),
            "day": b.get("day"),
            "type": b.get("type"),
            "name": b.get("name"),
        }
        for b in blocks
    ]
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": CLASSIFY_MODIFY_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"instruction": instruction, "blocks": brief}, ensure_ascii=False
                ),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=1000,
        timeout=60,
    )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {}
    return {
        "mode": result.get("mode") or "global",
        "targets": result.get("targets") or [],
    }


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
        if isinstance(data, dict) and data.get("classify"):
            kwargs: dict = {"api_key": os.getenv("OPENAI_API_KEY")}
            if os.getenv("OPENAI_BASE_URL"):
                kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
            client = OpenAI(**kwargs)
            result = classify_modify(
                client,
                data.get("instruction") or "",
                data.get("blocks") or [],
            )
            print(json.dumps(result, ensure_ascii=False))
            return

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
