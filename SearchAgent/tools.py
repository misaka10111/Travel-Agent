"""SearchAgent 的工具服务（MCP server）与确定性搜索编排。

结构：
- 结构化核心函数 ``_fetch_*``：返回 dict / list[dict]，供 JSON 输出与编排使用
- 文本格式化函数 ``_format_*``：把结构化数据转成可读文本
- MCP 工具：把结构化数据格式化成文本，供 LLM agent 调用
- ``run_search``：确定性综合编排，输入 JSON 输出 JSON（不经过 LLM）
"""

import json
import hashlib
import os
import re
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import OpenAI
from tavily import TavilyClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CACHE_DIR = BASE_DIR / "cache"
CACHE_TTL = 3600  # 缓存有效期（秒），1 小时

server = FastMCP(
    "search-tools",
    instructions="SearchAgent 的工具集",
    log_level="ERROR",
)

_tavily_client: TavilyClient | None = None


def _get_tavily() -> TavilyClient:
    """懒加载 Tavily 客户端，避免 import 阶段就要求 API key。"""
    global _tavily_client
    if _tavily_client is None:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ValueError("缺少 TAVILY_API_KEY，请在 SearchAgent/.env 中配置")
        _tavily_client = TavilyClient(api_key=api_key)
    return _tavily_client


POI_FEATURE_PROMPT = (
    "你是景点特征提取助手。为每个景点提取 3~6 个简短特征标签（逗号分隔，每个 2~4 字）。"
    "标签应覆盖：类型与属性（文化/历史/自然/亲子/宗教/演出/购物/美食/运动/科技/湖景/园林等）、"
    "环境（室内/户外）、适合人群（亲子/情侣/老人/学生等）。"
    "输出 JSON：{\"features\":[\"文化,历史,室内\",\"自然,湖景,户外\",...]}，顺序与输入景点一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

EVENTS_FEATURE_PROMPT = (
    "你是活动信息提炼助手。为每个活动提炼 3~6 个特征标签（逗号分隔，每个 2~4 字）。"
    "标签覆盖：活动类型（演唱会/音乐节/比赛/展览/节日/体育等）、室内外、适合人群、时间季节。"
    "输出 JSON：{\"features\":[\"音乐节,户外,10月\",...]}，顺序与输入一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

FOOD_FEATURE_PROMPT = (
    "你是美食信息提炼助手。为每个美食提炼 3~6 个特征标签（逗号分隔，每个 2~4 字）。"
    "标签覆盖：菜系、特色菜品、价位、环境、适合人群。"
    "输出 JSON：{\"features\":[\"杭帮菜,人均100,必吃\",...]}，顺序与输入一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)


FLYAI_BIN = Path(__file__).resolve().parent / "node_modules" / ".bin" / "flyai"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

WEATHER_CODES = {
    0: "晴朗",
    1: "基本晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "中阵雨",
    82: "强阵雨",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "SearchAgent/0.1"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_flyai(args: list[str]) -> dict:
    cmd = [str(FLYAI_BIN), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"flyai 调用失败：{detail}")
    return json.loads(result.stdout.strip())


def _normalize_date(value: str) -> str:
    """把各种日期格式统一成 YYYY-MM-DD。"""
    value = value.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", value)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", value)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", value)
    if m:
        return f"{date.today().year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?", value)
    if m:
        return f"{date.today().year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return value


def _geocode_city(city: str) -> tuple[str, str, str, str]:
    """地理编码：返回 (纬度, 经度, 名称, 国家)。"""
    query = urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "zh", "format": "json"}
    )
    geo = _http_get_json(f"{GEOCODING_URL}?{query}")
    results = geo.get("results") or []
    if not results:
        raise ValueError(f"未找到城市「{city}」，请换个写法试试。")
    place = results[0]
    return (
        str(place["latitude"]),
        str(place["longitude"]),
        place.get("name", city),
        place.get("country", ""),
    )


# ================= 结构化核心（返回 dict / list[dict]） =================


def _fetch_weather(city: str, start_date: str | None = None, end_date: str | None = None) -> dict:
    latitude, longitude, name, country = _geocode_city(city)
    today = date.today()
    try:
        start = date.fromisoformat(_normalize_date(start_date)) if start_date else today
        end = date.fromisoformat(_normalize_date(end_date)) if end_date else start
    except ValueError as exc:
        raise ValueError(f"日期无法识别：{start_date or end_date}") from exc

    base_url = ARCHIVE_URL if end < today else FORECAST_URL
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "auto",
    }
    data = _http_get_json(f"{base_url}?{urllib.parse.urlencode(params)}")
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    days = []
    for i, day in enumerate(times):
        code = daily.get("weather_code", [])[i]
        days.append(
            {
                "date": day,
                "weather": WEATHER_CODES.get(code, f"代码{code}"),
                "weather_code": code,
                "temp_min": daily.get("temperature_2m_min", [])[i],
                "temp_max": daily.get("temperature_2m_max", [])[i],
                "humidity": daily.get("relative_humidity_2m_mean", [])[i],
            }
        )
    return {
        "location": name,
        "country": country,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": days,
    }


def _fetch_hotels(
    destination: str,
    check_in_date: str | None = None,
    check_out_date: str | None = None,
    max_price: float | None = None,
    hotel_stars: str | None = None,
    hotel_types: str | None = None,
    sort: str | None = None,
) -> list[dict]:
    args = ["search-hotel", "--dest-name", destination]
    if check_in_date:
        args += ["--check-in-date", _normalize_date(check_in_date)]
    if check_out_date:
        args += ["--check-out-date", _normalize_date(check_out_date)]
    if max_price is not None:
        args += ["--max-price", str(int(max_price))]
    if hotel_stars:
        args += ["--hotel-stars", hotel_stars]
    if hotel_types:
        args += ["--hotel-types", hotel_types]
    if sort:
        args += ["--sort", sort]

    data = _run_flyai(args)
    if data.get("status") not in (0, None):
        raise ValueError(data.get("message") or "查询出错")
    items = (data.get("data") or {}).get("itemList") or []
    return [
        {
            "name": item.get("name") or "",
            "star": item.get("star") or "",
            "score": item.get("scoreDesc") or item.get("score") or "",
            "price": item.get("price") or "",
            "location": item.get("interestsPoi") or item.get("address") or "",
            "url": item.get("detailUrl") or "",
        }
        for item in items
    ]


def _fetch_flights(
    origin: str,
    destination: str | None = None,
    dep_date: str | None = None,
    back_date: str | None = None,
    journey_type: str | None = None,
    sort_type: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    args = ["search-flight", "--origin", origin]
    if destination:
        args += ["--destination", destination]
    if dep_date:
        args += ["--dep-date", _normalize_date(dep_date)]
    if back_date:
        args += ["--back-date", _normalize_date(back_date)]
    if journey_type:
        args += ["--journey-type", journey_type]
    if sort_type:
        args += ["--sort-type", sort_type]
    if max_price is not None:
        args += ["--max-price", str(int(max_price))]

    data = _run_flyai(args)
    if data.get("status") not in (0, None):
        raise ValueError(data.get("message") or "查询出错")
    items = (data.get("data") or {}).get("itemList") or []
    result = []
    for item in items:
        journeys = item.get("journeys") or []
        segment = (journeys[0].get("segments") or [{}])[0] if journeys else {}
        arr_city = segment.get("arrCityName") or ""
        if destination and arr_city and destination not in arr_city:
            continue
        result.append(
            {
                "airline": segment.get("marketingTransportName") or "",
                "flight_no": segment.get("marketingTransportNo") or "",
                "dep_station": segment.get("depStationName") or "",
                "arr_station": segment.get("arrStationName") or "",
                "dep_time": segment.get("depDateTime") or "",
                "arr_time": segment.get("arrDateTime") or "",
                "seat": segment.get("seatClassName") or "",
                "duration": item.get("totalDuration") or "",
                "price": item.get("ticketPrice") or item.get("adultPrice") or "",
                "url": item.get("jumpUrl") or "",
            }
        )
    return result


def _fetch_trains(
    origin: str,
    destination: str | None = None,
    dep_date: str | None = None,
    journey_type: str | None = None,
    sort_type: str | None = None,
) -> list[dict]:
    """搜索高铁/火车票（飞猪 search-train）。"""
    args = ["search-train", "--origin", origin]
    if destination:
        args += ["--destination", destination]
    if dep_date:
        args += ["--dep-date", _normalize_date(dep_date)]
    if journey_type:
        args += ["--journey-type", journey_type]
    if sort_type:
        args += ["--sort-type", sort_type]

    data = _run_flyai(args)
    if data.get("status") not in (0, None):
        raise ValueError(data.get("message") or "查询出错")
    items = (data.get("data") or {}).get("itemList") or []
    result = []
    for item in items:
        journeys = item.get("journeys") or []
        segment = (journeys[0].get("segments") or [{}])[0] if journeys else {}
        arr_city = segment.get("arrCityName") or ""
        if destination and arr_city and destination not in arr_city:
            continue
        result.append(
            {
                "transport": segment.get("marketingTransportName") or "火车",
                "train_no": segment.get("marketingTransportNo") or "",
                "dep_station": segment.get("depStationName") or "",
                "arr_station": segment.get("arrStationName") or "",
                "dep_time": segment.get("depDateTime") or "",
                "arr_time": segment.get("arrDateTime") or "",
                "seat": segment.get("seatClassName") or "",
                "duration": item.get("totalDuration") or "",
                "price": item.get("price") or "",
                "url": item.get("jumpUrl") or "",
            }
        )
    return result


def _fetch_round_trip(
    fetcher,
    origin: str,
    destination: str,
    start: str,
    end: str,
) -> list[dict]:
    """查去程（origin→destination，start 出发）+ 回程（反向，end 出发），每条带 direction。"""
    outbound = fetcher(origin, destination, start, journey_type="1")
    for x in outbound:
        x["direction"] = "去"
    inbound = []
    if end != start:
        inbound = fetcher(destination, origin, end, journey_type="1")
        for x in inbound:
            x["direction"] = "回"
    return outbound + inbound


def _extract_poi_features(pois: list[dict]) -> list[dict]:
    """用 LLM 为每个景点提取简短特征标签，替换长 description；失败则回退截断。"""
    if not pois:
        return pois
    try:
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        brief = [
            {
                "name": p.get("name") or "",
                "category": p.get("category") or "",
                "description": (p.get("description") or "")[:200],
            }
            for p in pois
        ]
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": POI_FEATURE_PROMPT},
                {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=6000,
            timeout=60,
        )
        content = (resp.choices[0].message.content or "{}").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        features = json.loads(content).get("features") or []
        for i, p in enumerate(pois):
            p["description"] = features[i] if i < len(features) else ""
        return pois
    except Exception:
        for p in pois:
            p["description"] = (p.get("description") or "")[:80]
        return pois


def _extract_item_features(items: list[dict], prompt: str) -> list[dict]:
    """用 LLM 为活动/美食提炼特征标签，替换长 content；失败则回退截断。"""
    if not items:
        return items
    try:
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        brief = [
            {
                "title": x.get("title") or "",
                "content": (x.get("content") or "")[:200],
            }
            for x in items
        ]
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=6000,
            timeout=60,
        )
        content = (resp.choices[0].message.content or "{}").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        features = json.loads(content).get("features") or []
        for i, x in enumerate(items):
            x["content"] = features[i] if i < len(features) else ""
        return items
    except Exception:
        for x in items:
            x["content"] = (x.get("content") or "")[:80]
        return items


def _fetch_poi(
    city_name: str,
    keyword: str | None = None,
    category: str | None = None,
    poi_level: str | None = None,
) -> list[dict]:
    args = ["search-poi", "--city-name", city_name]
    if keyword:
        args += ["--keyword", keyword]
    if category:
        args += ["--category", category]
    if poi_level:
        args += ["--poi-level", poi_level]

    data = _run_flyai(args)
    if data.get("status") not in (0, None):
        raise ValueError(data.get("message") or "查询出错")
    items = (data.get("data") or {}).get("itemList") or []
    pois = [
        {
            "name": item.get("name") or "",
            "category": item.get("category") or "",
            "rank": item.get("listRank") or "",
            "free": item.get("freePoiStatus") == "FREE",
            "description": item.get("description") or "",
            "url": item.get("jumpUrl") or "",
        }
        for item in items
    ]
    return _extract_poi_features(pois)


def _fetch_promotions(keyword: str | None = None) -> list[dict]:
    """检索飞猪促销活动/优惠商品（特价机票卡、券包、酒店套餐等）。"""
    query = (keyword or "").strip() or "促销活动 特价 优惠"
    data = _run_flyai(["keyword-search", "--query", query])
    if data.get("status") not in (0, None):
        raise ValueError(data.get("message") or "查询出错")
    items = (data.get("data") or {}).get("itemList") or []
    result = []
    for item in items:
        info = item.get("info") or {}
        result.append(
            {
                "title": info.get("title") or "",
                "price": info.get("price") or "",
                "star": info.get("star") or "",
                "tags": info.get("tags") or "",
                "image": info.get("picUrl") or "",
                "url": info.get("jumpUrl") or "",
            }
        )
    return result


def _fetch_web_search(
    query: str,
    max_results: int = 10,
    search_depth: str = "advanced",
) -> list[dict]:
    """通用网页搜索（Tavily），返回 LLM 优化的结果。"""
    client = _get_tavily()
    resp = client.search(
        query=query,
        max_results=max_results,
        search_depth=search_depth,
    )
    return [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "content": (r.get("content") or "")[:600],
            "score": r.get("score"),
        }
        for r in (resp.get("results") or [])
    ]


def _fetch_events(
    destination: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_results: int = 10,
) -> list[dict]:
    """搜索某地在日期段内的热点活动（演唱会/比赛/节日等），复用 Tavily。"""
    start = _normalize_date(start_date) if start_date else ""
    end = _normalize_date(end_date) if end_date else ""
    if start and end:
        date_range = f"{start}到{end}"
    elif start:
        date_range = start
    else:
        date_range = "近期"
    query = f"{destination} {date_range} 演唱会 音乐节 比赛 展览 节日 活动 热点"
    items = _fetch_web_search(query, max_results=max_results)
    return _extract_item_features(items, EVENTS_FEATURE_PROMPT)


def _fetch_food(destination: str, max_results: int = 10) -> list[dict]:
    """搜索当地美食（大众点评/小红书/抖音等平台），复用 Tavily。"""
    query = f"{destination} 美食 必吃 餐厅 小吃 大众点评 小红书 抖音 探店"
    items = _fetch_web_search(query, max_results=max_results)
    return _extract_item_features(items, FOOD_FEATURE_PROMPT)


# ================= 文本格式化（MCP 工具用） =================


def _format_weather(d: dict) -> str:
    lines = [f"{d['location']}（{d['country']}）{d['start_date']} 至 {d['end_date']} 逐日天气："]
    for day in d["days"]:
        lines.append(
            f"  {day['date']}：{day['weather']}，{day['temp_min']}~{day['temp_max']}°C，湿度 {day['humidity']}%"
        )
    return "\n".join(lines) if d["days"] else f"{d['location']} 该日期暂无天气数据。"


def _format_hotels(items: list[dict], destination: str) -> str:
    if not items:
        return f"没有找到「{destination}」的酒店。"
    lines = [f"{destination} 酒店（前 {min(len(items), 5)} 家）："]
    for item in items[:5]:
        parts = [item["name"]]
        for key in ("star", "score", "price", "location"):
            if item.get(key):
                parts.append(str(item[key]))
        lines.append(" - " + " | ".join(parts))
        if item.get("url"):
            lines.append(f"   预订: {item['url']}")
    return "\n".join(lines)


def _format_flights(items: list[dict], origin: str, destination: str | None) -> str:
    label = f"{origin} → {destination or '目的地'}"
    if not items:
        return f"没有找到 {label} 的机票。"
    lines = [f"{label} 机票（前 {min(len(items), 5)} 班）："]
    for item in items[:5]:
        core = f"{item['airline']}{item['flight_no']} | {item['dep_station']}→{item['arr_station']} | {item['dep_time']} → {item['arr_time']}"
        parts = [core]
        if item.get("seat"):
            parts.append(item["seat"])
        if item.get("duration"):
            parts.append(f"{item['duration']}分钟")
        if item.get("price"):
            parts.append(f"¥{item['price']}")
        lines.append(" - " + " | ".join(parts))
        if item.get("url"):
            lines.append(f"   预订: {item['url']}")
    return "\n".join(lines)


def _format_trains(items: list[dict], origin: str, destination: str | None) -> str:
    label = f"{origin} → {destination or '目的地'}"
    if not items:
        return f"没有找到 {label} 的高铁/火车票。"
    lines = [f"{label} 高铁/火车票（前 {min(len(items), 5)} 班）："]
    for item in items[:5]:
        core = f"{item['transport']}{item['train_no']} | {item['dep_station']}→{item['arr_station']} | {item['dep_time']} → {item['arr_time']}"
        parts = [core]
        if item.get("seat"):
            parts.append(item["seat"])
        if item.get("duration"):
            parts.append(f"{item['duration']}分钟")
        if item.get("price"):
            parts.append(f"¥{item['price']}")
        lines.append(" - " + " | ".join(parts))
        if item.get("url"):
            lines.append(f"   预订: {item['url']}")
    return "\n".join(lines)


def _format_poi(items: list[dict], city_name: str) -> str:
    if not items:
        return f"没有找到「{city_name}」的景点。"
    lines = [f"{city_name} 景点（前 {min(len(items), 10)} 个）："]
    for item in items[:10]:
        parts = [item["name"]]
        if item.get("category"):
            parts.append(item["category"])
        if item.get("rank"):
            parts.append(item["rank"])
        if item.get("free"):
            parts.append("免费")
        lines.append(" - " + " | ".join(parts))
        if item.get("description"):
            lines.append(f"   {item['description'][:100]}")
        if item.get("url"):
            lines.append(f"   预订: {item['url']}")
    return "\n".join(lines)


def _format_promotions(items: list[dict], keyword: str) -> str:
    if not items:
        return f"没有找到「{keyword}」相关的促销活动。"
    lines = [f"飞猪促销活动（前 {min(len(items), 10)} 个）："]
    for item in items[:10]:
        parts = [item["title"]]
        if item.get("price"):
            parts.append(str(item["price"]))
        if item.get("star"):
            parts.append(str(item["star"]))
        lines.append(" - " + " | ".join(parts))
        if item.get("url"):
            lines.append(f"   预订: {item['url']}")
    return "\n".join(lines)


def _format_web_search(items: list[dict], query: str) -> str:
    if not items:
        return f"没有找到「{query}」相关的网页结果。"
    lines = [f"「{query}」网页搜索结果（前 {min(len(items), 10)} 条）："]
    for item in items[:10]:
        lines.append(f" - {item['title']}")
        if item.get("content"):
            lines.append(f"   {item['content'][:180]}")
        if item.get("url"):
            lines.append(f"   {item['url']}")
    return "\n".join(lines)


def _format_events(items: list[dict], destination: str, start_date: str, end_date: str) -> str:
    label = f"{_normalize_date(start_date)} 至 {_normalize_date(end_date)}"
    if not items:
        return f"没有找到「{destination} {label}」的热点活动。"
    lines = [f"{destination} 热点活动（{label}，前 {min(len(items), 10)} 条）："]
    for item in items[:10]:
        lines.append(f" - {item['title']}")
        if item.get("content"):
            lines.append(f"   {item['content'][:180]}")
        if item.get("url"):
            lines.append(f"   {item['url']}")
    return "\n".join(lines)


def _format_food(items: list[dict], destination: str) -> str:
    if not items:
        return f"没有找到「{destination}」的美食推荐。"
    lines = [f"{destination} 美食推荐（前 {min(len(items), 10)} 条）："]
    for item in items[:10]:
        lines.append(f" - {item['title']}")
        if item.get("content"):
            lines.append(f"   {item['content'][:180]}")
        if item.get("url"):
            lines.append(f"   {item['url']}")
    return "\n".join(lines)


# ================= MCP 工具（返回文本） =================


@server.tool(
    description="查询某城市在指定日期（段）内的逐日天气。city 必填，start_date/end_date 可选（YYYY-MM-DD）。"
)
def get_weather(city: str, start_date: str | None = None, end_date: str | None = None) -> str:
    return _format_weather(_fetch_weather(city, start_date, end_date))


@server.tool(description="搜索目的地酒店。destination 必填，其余可选。")
def search_hotels(
    destination: str,
    check_in_date: str | None = None,
    check_out_date: str | None = None,
    max_price: float | None = None,
    hotel_stars: str | None = None,
    hotel_types: str | None = None,
    sort: str | None = None,
) -> str:
    return _format_hotels(
        _fetch_hotels(destination, check_in_date, check_out_date, max_price, hotel_stars, hotel_types, sort),
        destination,
    )


@server.tool(description="搜索机票。origin 必填，其余可选。")
def search_flights(
    origin: str,
    destination: str | None = None,
    dep_date: str | None = None,
    back_date: str | None = None,
    journey_type: str | None = None,
    sort_type: str | None = None,
    max_price: float | None = None,
) -> str:
    return _format_flights(
        _fetch_flights(origin, destination, dep_date, back_date, journey_type, sort_type, max_price),
        origin,
        destination,
    )


@server.tool(description="搜索高铁/火车票。origin 必填，其余可选。")
def search_trains(
    origin: str,
    destination: str | None = None,
    dep_date: str | None = None,
    sort_type: str | None = None,
) -> str:
    return _format_trains(
        _fetch_trains(origin, destination, dep_date, sort_type),
        origin,
        destination,
    )


@server.tool(description="搜索景点/风景名胜。city_name 必填，其余可选。")
def search_poi(
    city_name: str,
    keyword: str | None = None,
    category: str | None = None,
    poi_level: str | None = None,
) -> str:
    return _format_poi(_fetch_poi(city_name, keyword, category, poi_level), city_name)


@server.tool(description="检索飞猪促销活动/优惠商品（特价机票卡、券包、酒店套餐等）。keyword 可选。")
def search_promotions(keyword: str | None = None) -> str:
    return _format_promotions(_fetch_promotions(keyword), keyword or "促销活动")


@server.tool(
    description="通用网页搜索（Tavily），返回 LLM 优化的搜索结果摘要。query 必填，max_results 可选（默认 10）。"
)
def search_web(query: str, max_results: int = 10) -> str:
    return _format_web_search(_fetch_web_search(query, max_results), query)


@server.tool(
    description="搜索某地在日期段内的热点活动（演唱会/音乐节/比赛/展览/节日等）。destination、start_date、end_date 必填（YYYY-MM-DD）。"
)
def search_events(destination: str, start_date: str, end_date: str, max_results: int = 10) -> str:
    return _format_events(
        _fetch_events(destination, start_date, end_date, max_results),
        destination,
        start_date,
        end_date,
    )


@server.tool(description="搜索当地美食（大众点评/小红书/抖音等平台）。destination 必填。")
def search_food(destination: str, max_results: int = 10) -> str:
    return _format_food(_fetch_food(destination, max_results), destination)


# ================= 确定性综合编排（JSON in / JSON out） =================


def _run_safe(key: str, fn: Any) -> tuple[str, Any]:
    try:
        return key, fn()
    except Exception as exc:  # noqa: BLE001
        return key, {"error": str(exc)}


def _cache_key(destination: str, start: str, end: str, origin: str) -> str:
    raw = "|".join([destination, start, end, origin])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _read_cache(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(key: str, result: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def run_search(input_data: dict) -> dict:
    """输入 {destination, start_date, end_date?, origin?}，并行调用 6~8 个工具，返回结构化 JSON。"""
    destination = (input_data.get("destination") or "").strip()
    if not destination:
        raise ValueError("缺少 destination")
    start_date = input_data.get("start_date")
    if not start_date:
        raise ValueError("缺少 start_date")
    start = _normalize_date(start_date)
    end = _normalize_date(input_data["end_date"]) if input_data.get("end_date") else start
    origin = (input_data.get("origin") or "").strip()

    # 缓存：同一目的地/日期/出发地的结果直接复用，避免反复调用飞猪/Tavily/天气
    cache_key = _cache_key(destination, start, end, origin)
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "destination": destination,
        "start_date": start,
        "end_date": end,
    }
    if origin:
        result["origin"] = origin

    tasks = {
        "weather": lambda: _fetch_weather(destination, start, end),
        "hotels": lambda: _fetch_hotels(destination, start, end),
        "poi": lambda: _fetch_poi(destination),
        "promotions": lambda: _fetch_promotions(f"{destination} 促销 特价"),
        "events": lambda: _fetch_events(destination, start, end),
        "food": lambda: _fetch_food(destination),
    }
    if origin:
        tasks["flights"] = lambda: _fetch_round_trip(_fetch_flights, origin, destination, start, end)
        tasks["trains"] = lambda: _fetch_round_trip(_fetch_trains, origin, destination, start, end)
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {key: executor.submit(_run_safe, key, fn) for key, fn in tasks.items()}
        for key, future in futures.items():
            result_key, value = future.result()
            result[result_key] = value

    _write_cache(cache_key, result)
    return result


@server.tool(description="综合搜索某目的地（天气+酒店+景点+促销），返回结构化 JSON。")
def search_trip(destination: str, start_date: str, end_date: str | None = None) -> str:
    return json.dumps(
        run_search({"destination": destination, "start_date": start_date, "end_date": end_date}),
        ensure_ascii=False,
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
