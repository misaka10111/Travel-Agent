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
import sys
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

from amap_service import search_restaurants

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parent))
from shared.pricing import parse_price
load_dotenv(BASE_DIR / ".env")

CACHE_DIR = BASE_DIR / "cache"
CACHE_TTL = 3600  # 缓存有效期（秒），1 小时
SEARCH_VERSION = "hotel30-v8"  # 搜索逻辑版本，变更后自动让旧缓存失效

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
    "同时估计每个景点的建议游玩时长（可选值：0.5-1小时、1-2小时、2-3小时、半天、全天）"
    "和景点规模（大/中/小，大=大型景区或需要大量步行，小=单点或小展馆）。"
    "输出 JSON：{\"features\":[\"文化,历史,室内\",...],\"durations\":[\"2-3小时\",...],\"scales\":[\"中\",...]}，"
    "三个数组顺序都与输入景点一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

EVENTS_FEATURE_PROMPT = (
    "你是活动信息提炼助手。为每个活动提炼 3~6 个特征标签（逗号分隔，每个 2~4 字）。"
    "标签覆盖：活动类型（演唱会/音乐节/比赛/展览/节日/体育等）、室内外、适合人群、时间季节。"
    "输出 JSON：{\"features\":[\"音乐节,户外,10月\",...]}，顺序与输入一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

EVENT_EXTRACT_PROMPT = (
    "你是活动信息提取助手。从网页搜索结果中提取具体的活动。"
    "输出 JSON：{\"events\":[{\"name\":\"具体活动名称，如「某某演唱会」\","
    "\"type\":\"演唱会/音乐节/比赛/展览/节日等\",\"date\":\"活动日期或时间\","
    "\"url\":\"对应活动链接\"}]}。"
    "只保留具体活动，去掉「活动汇总」「榜单」「门户首页」等非具体活动；"
    "如果某条结果本身就是具体活动，保留它的标题作为 name。"
    "最多返回 20 个，只输出 JSON，不要任何多余文字。"
)

FOOD_FEATURE_PROMPT = (
    "你是美食信息提炼助手。为每个美食提炼 3~6 个特征标签（逗号分隔，每个 2~4 字）。"
    "标签覆盖：菜系、特色菜品、价位、环境、适合人群。"
    "输出 JSON：{\"features\":[\"杭帮菜,人均100,必吃\",...]}，顺序与输入一致。"
    "只输出 JSON，不要任何多余文字或代码块。"
)

RESTAURANT_PROMPT = (
    "你是美食餐厅提取助手。根据多个社交平台的搜索结果，提取当地美食的候选餐厅。"
    "输出 JSON：{\"food\":[{\"category\":\"菜系或类型\",\"options\":["
    "{\"name\":\"餐厅名\",\"link\":\"平台链接\",\"source\":\"抖音/大众点评/小红书\"},"
    "{\"name\":\"餐厅名2\",\"link\":\"链接2\",\"source\":\"平台\"}]}]}。"
    "每个 category 返回两个候选餐厅（options），链接优先来自抖音/大众点评/小红书的具体内容链接，"
    "找不到对应平台链接就留空字符串。提取 4~5 个 category，共 8~10 个餐厅。"
    "餐厅名和链接尽量来自搜索结果原文，不要编造。只输出 JSON，不要任何多余文字或代码块。"
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


def _fetch_hotels_once(
    destination: str,
    check_in_date: str | None = None,
    check_out_date: str | None = None,
    max_price: float | None = None,
    hotel_stars: str | None = None,
    hotel_types: str | None = None,
    key_words: str | None = None,
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
    if key_words:
        args += ["--key-words", key_words]
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
            "longitude": item.get("longitude"),
            "latitude": item.get("latitude"),
            "image": (
                item.get("mainPic")
                or item.get("picUrl")
                or item.get("pictureUrl")
                or item.get("imgUrl")
                or item.get("imageUrl")
                or item.get("mainImage")
                or ""
            ),
        }
        for item in items
    ]


def _fetch_hotels(
    destination: str,
    check_in_date: str | None = None,
    check_out_date: str | None = None,
    max_price: float | None = None,
    hotel_stars: str | None = None,
    hotel_types: str | None = None,
    key_words: str | None = None,
    sort: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """搜索酒店：飞猪无分页，用多种排序多次搜索后去重，尽量凑到 limit 条。"""
    if sort:
        return _fetch_hotels_once(
            destination,
            check_in_date,
            check_out_date,
            max_price,
            hotel_stars,
            hotel_types,
            key_words,
            sort,
        )[:limit]

    seen: set[str] = set()
    result: list[dict] = []
    rounds = [
        {"sort": "rate_desc"},
        {"sort": "rate_desc", "hotel_stars": "5"},
        {"sort": "rate_desc", "hotel_stars": "4"},
        {"sort": "price_asc", "hotel_stars": "3"},
        {"sort": "no_rank", "key_words": "如家 汉庭 全季 亚朵 维也纳"},
    ]
    for params in rounds:
        try:
            items = _fetch_hotels_once(
                destination,
                check_in_date,
                check_out_date,
                max_price,
                params.get("hotel_stars") or hotel_stars,
                hotel_types,
                params.get("key_words") or key_words,
                params.get("sort") or sort,
            )
        except Exception:
            items = []
        for item in items:
            name = item.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(item)
            if len(result) >= limit:
                break
        if len(result) >= limit:
            break
    return result


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
            max_tokens=12000,
            timeout=60,
        )
        content = (resp.choices[0].message.content or "{}").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        features = json.loads(content).get("features") or []
        durations = json.loads(content).get("durations") or []
        scales = json.loads(content).get("scales") or []
        for i, p in enumerate(pois):
            p["description"] = features[i] if i < len(features) else ""
            p["duration"] = durations[i] if i < len(durations) else ""
            p["scale"] = scales[i] if i < len(scales) else ""
        return pois
    except Exception:
        for p in pois:
            p["description"] = (p.get("description") or "")[:80]
            p.setdefault("duration", "")
            p.setdefault("scale", "")
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


POI_THEMES = {
    "自然户外": ["自然风光", "山湖田园", "户外活动"],
    "历史文化": ["人文古迹", "宗教场所", "园林花园", "古镇古村"],
    "教育博物": ["博物馆", "纪念馆"],
    "城市生活": ["文创街区", "市集"],
}

STYLE_KEYWORDS = {
    "亲子乐园": ["亲子", "乐园", "动物园", "海洋馆"],
    "深度文化": ["博物馆", "历史古迹", "纪念馆"],
    "自然风光": ["自然风光", "公园", "山湖田园"],
    "美食探店": ["美食街区", "市集", "老字号"],
    "摄影旅拍": ["网红打卡", "地标", "拍照出片"],
    "冒险户外": ["户外活动", "徒步", "露营"],
    "购物血拼": ["商圈", "购物中心"],
    "休闲度假": ["湖景", "园林", "温泉"],
}


def _profile_keywords(profile: dict | None) -> list[str]:
    if not profile:
        return []
    styles = profile.get("travel_style") or []
    if isinstance(styles, str):
        styles = [styles]
    keywords: list[str] = []
    for style in styles:
        keywords.extend(STYLE_KEYWORDS.get(style, []))
    return keywords
def _pick_district(item: dict) -> str:
    for key in ("districtName", "district", "areaName", "region", "address"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _extract_district_label(value: str) -> str:
    """从地址里抽出区县级行政区，如「西湖区」「淳安县」。"""
    value = (value or "").strip()
    if not value:
        return ""
    candidates: list[tuple[int, str]] = []
    for candidate in ("自治县", "区", "县", "旗"):
        start = 0
        while True:
            idx = value.find(candidate, start)
            if idx == -1:
                break
            # 跳过「自治区」里的「区」
            if candidate == "区" and idx >= 2 and value[idx - 2:idx] == "自治":
                start = idx + 1
                continue
            candidates.append((idx, candidate))
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


def _poi_hot_key(item: dict):
    match = re.search(r"第\s*(\d+)\s*名", str(item.get("rank") or ""))
    if not match:
        return (1, 0)
    return (0, int(match.group(1)))


def _search_poi_items(
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
    pois = []
    for item in items:
        raw_district = _pick_district(item)
        address = item.get("address") or ""
        pois.append(
            {
            "name": item.get("name") or "",
            "category": item.get("category") or "",
            "rank": item.get("listRank") or "",
            "free": item.get("freePoiStatus") == "FREE",
            "description": item.get("description") or "",
            "url": item.get("jumpUrl") or "",
            "longitude": item.get("longitude"),
            "latitude": item.get("latitude"),
            "image": (
                item.get("mainPic")
                or item.get("picUrl")
                or item.get("pictureUrl")
                or item.get("imgUrl")
                or item.get("imageUrl")
                or item.get("mainImage")
                or ""
            ),
            "district": raw_district,
            "district_label": _extract_district_label(raw_district)
            or _extract_district_label(address),
        }
        )
    return pois


def _fetch_poi(
    city_name: str,
    keyword: str | None = None,
    category: str | None = None,
    poi_level: str | None = None,
) -> list[dict]:
    pois = _search_poi_items(city_name, keyword, category, poi_level)
    return _extract_poi_features(pois)


def _fetch_poi_distributed(
    city_name: str,
    target: int = 50,
    extra_keywords: list[str] | None = None,
) -> list[dict]:
    """按 4 个主题分散搜索景点，合并去重后均匀取 target 条。"""
    by_category: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for theme, categories in POI_THEMES.items():
        for category in categories:
            try:
                items = _search_poi_items(city_name, category=category)
            except Exception:
                items = []
            for item in items:
                name = (item.get("name") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                item["query_category"] = theme
                by_category.setdefault(theme, []).append(item)

    # 补上飞猪默认热门榜（西湖、雷峰塔、灵隐寺这类经典景点）
    try:
        default_items = _search_poi_items(city_name)
    except Exception:
        default_items = []
    for item in default_items:
        name = (item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        item["query_category"] = "经典必去"
        by_category.setdefault("经典必去", []).append(item)

    # 再用关键词补搜地标/必去，避免漏掉西湖这类超经典景点
    for keyword in ("必去", "地标"):
        try:
            extra_items = _search_poi_items(city_name, keyword=keyword)
        except Exception:
            extra_items = []
        for item in extra_items:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item["query_category"] = "经典必去"
            by_category.setdefault("经典必去", []).append(item)

    # 根据用户画像关键词，各多检索 10 条
    for keyword in extra_keywords or []:
        try:
            extra_items = _search_poi_items(city_name, keyword=keyword)
        except Exception:
            extra_items = []
        for item in extra_items[:10]:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item["query_category"] = "用户偏好"
            by_category.setdefault("用户偏好", []).append(item)

    themes = [
        t for t in list(POI_THEMES) + ["经典必去", "用户偏好"]
        if by_category.get(t)
    ]
    idx = {t: 0 for t in themes}
    district_counts: dict[str, int] = {}
    selected: list[dict] = []

    def district_key(item: dict) -> str:
        return item.get("district_label") or item.get("district") or ""

    while len(selected) < target and themes:
        progressed = False
        for theme in themes:
            if len(selected) >= target:
                break
            items = by_category[theme]
            if idx[theme] >= len(items):
                continue
            # 在剩余候选中优先选一个当前出现次数更少的行政区，促进城市内分布均匀
            candidates = items[idx[theme]:]
            best = min(
                candidates,
                key=lambda p: district_counts.get(district_key(p), 0),
            )
            best_pos = items.index(best, idx[theme])
            items[idx[theme]], items[best_pos] = items[best_pos], items[idx[theme]]
            picked = items[idx[theme]]
            idx[theme] += 1
            selected.append(picked)
            district = district_key(picked)
            if district:
                district_counts[district] = district_counts.get(district, 0) + 1
            progressed = True
        if not progressed:
            break

    selected.sort(key=_poi_hot_key)
    return _extract_poi_features(selected)


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


def _extract_events(items: list[dict]) -> list[dict]:
    """从 Tavily 网页结果中提取具体活动名称、类型、日期和链接。"""
    if not items:
        return []
    try:
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        brief = [
            {"title": x.get("title") or "", "url": x.get("url") or "", "content": (x.get("content") or "")[:300]}
            for x in items
        ]
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": EVENT_EXTRACT_PROMPT},
                {"role": "user", "content": json.dumps({"results": brief}, ensure_ascii=False)},
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
        events = json.loads(content).get("events") or []
        result = []
        for e in events:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            typ = (e.get("type") or "").strip()
            date_ = (e.get("date") or "").strip()
            result.append(
                {
                    "title": name,
                    "content": f"{typ},{date_}" if typ or date_ else "",
                    "url": e.get("url") or "",
                }
            )
        return result
    except Exception:
        return [
            {
                "title": x.get("title") or "",
                "content": (x.get("content") or "")[:120],
                "url": x.get("url") or "",
            }
            for x in items
        ]


def _fetch_events(
    destination: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_results: int = 20,
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
    return _extract_events(items)


def _fetch_food(
    destination: str,
    keyword: str | None = None,
    max_price: float | None = None,
    max_results: int = 50,
) -> list[dict]:
    """用高德地图 POI 搜索当地餐厅，返回结构化数据。

    优先调用高德（返回 name/address/lng/lat/cuisine/rating/人均/商圈/地图链接/POI详情链接）；
    若未配置 AMAP_KEY 或调用失败，回退到 Tavily 网页搜索，并在结果中标记来源。

    每条结果带 _source 字段："amap" 或 "tavily"，便于下游区分数据格式。
    """
    # 1. 优先高德
    try:
        restaurants = search_restaurants(
            destination,
            keyword=keyword,
            limit=max_results,
        )
        if restaurants:
            items = [r.to_dict() for r in restaurants]
            # 按人均预算过滤（如果有）
            if max_price is not None and max_price > 0:
                items = [
                    it for it in items
                    if it.get("price_per_person") is None or it["price_per_person"] <= max_price
                ]
            for it in items:
                it["_source"] = "amap"
            return items
        # 高德返回空也算失败，走回退
        raise RuntimeError("高德返回 0 条餐厅结果")
    except Exception as exc:  # noqa: BLE001
        amap_error = str(exc)
        print(f"[food] 高德搜索失败，回退 Tavily：{amap_error}", file=sys.stderr)

    # 2. 回退：Tavily 网页搜索
    query = f"{destination} 美食 必吃 餐厅 小吃"
    if keyword:
        query += f" {keyword}"
    items = _fetch_web_search(query, max_results=min(max_results, 20))
    items = _extract_item_features(items, FOOD_FEATURE_PROMPT)
    for it in items:
        it["_source"] = "tavily"
    return items


def _fetch_social_food(destination: str, max_results: int = 20) -> list[dict]:
    """在抖音/小红书搜索当地美食视频或笔记链接。"""
    queries = {
        "抖音": f"{destination} 美食 探店 抖音",
        "小红书": f"{destination} 美食 探店 小红书 笔记",
    }
    results: list[dict] = []
    for platform, query in queries.items():
        try:
            items = _fetch_web_search(query, max_results=max_results // 2)
        except Exception:
            items = []
        for item in items:
            url = (item.get("url") or "").lower()
            if platform == "抖音" and "douyin.com" not in url:
                continue
            if platform == "小红书" and "xiaohongshu.com" not in url and "xhslink.com" not in url:
                continue
            results.append(
                {
                    "platform": platform,
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "content": (item.get("content") or "")[:200],
                }
            )
    return results


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
        return f"没有找到「{destination}」的餐厅。"
    # 高德结构化数据有 name/cuisine/rating 等字段；Tavily 回退数据只有 title/content
    is_amap = any("cuisine" in item for item in items)
    lines = [f"{destination} 餐厅推荐（前 {min(len(items), 10)} 家）："]
    for item in items[:10]:
        if is_amap:
            parts = [item.get("name", "")]
            if item.get("cuisine"):
                parts.append(f"菜系:{item['cuisine']}")
            if item.get("rating"):
                parts.append(f"评分:{item['rating']}")
            if item.get("price_per_person"):
                parts.append(f"人均:¥{item['price_per_person']}")
            if item.get("business_area"):
                parts.append(f"商圈:{item['business_area']}")
            if item.get("address"):
                parts.append(f"地址:{item['address']}")
            lines.append(" - " + " | ".join(p for p in parts if p))
            if item.get("map_url"):
                lines.append(f"   地图: {item['map_url']}")
            if item.get("poi_detail_url"):
                lines.append(f"   详情: {item['poi_detail_url']}")
        else:
            lines.append(f" - {item.get('title', '')}")
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


@server.tool(description="搜索目的地餐厅（高德地图），返回名称、菜系、评分、人均、地址、商圈和地图/详情链接。destination 必填。")
def search_food(destination: str, max_results: int = 10) -> str:
    return _format_food(_fetch_food(destination, max_results), destination)


# ================= 确定性综合编排（JSON in / JSON out） =================


def _run_safe(key: str, fn: Any) -> tuple[str, Any]:
    try:
        return key, fn()
    except Exception as exc:  # noqa: BLE001
        return key, {"error": str(exc)}


def _cache_key(destination: str, start: str, end: str, origin: str, extra: str = "") -> str:
    raw = "|".join([SEARCH_VERSION, destination, start, end, origin, extra])
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
    """输入 {destination, start_date, end_date?, origin?, basic?, food_keyword?}，
    并行调用 6~8 个工具，返回结构化 JSON。

    basic 可含 total_budget（总预算）、travelers（出行人数）、purposes（旅行目的），
    用于推算餐饮搜索的人均预算上限。
    food_keyword 为用户明确提到的菜系/关键词（如 "川菜"、"火锅"），会透传给高德搜索。
    """
    destination = (input_data.get("destination") or "").strip()
    if not destination:
        raise ValueError("缺少 destination")
    start_date = input_data.get("start_date")
    if not start_date:
        raise ValueError("缺少 start_date")
    start = _normalize_date(start_date)
    end = _normalize_date(input_data["end_date"]) if input_data.get("end_date") else start
    origin = (input_data.get("origin") or "").strip()

    # 从 basic 中提取餐饮搜索参数
    basic = input_data.get("basic") or {}
    food_keyword = (input_data.get("food_keyword") or "").strip() or None
    max_price = _estimate_food_budget(basic)

    # 缓存：把餐饮搜索参数纳入 key，不同菜系/预算不串缓存
    cache_key = _cache_key(
        destination, start, end, origin,
        extra=f"{food_keyword or ''}|{max_price or ''}",
    )
    cached = None if input_data.get("force_refresh") else _read_cache(cache_key)
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
        "poi": lambda: _fetch_poi_distributed(
            destination,
            target=50,
            extra_keywords=_profile_keywords(input_data.get("profile")),
        ),
        "events": lambda: _fetch_events(destination, start, end),
        "food": lambda: _fetch_food(
            destination,
            keyword=food_keyword,
            max_price=max_price,
        ),
        "social_food": lambda: _fetch_social_food(destination),
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


def _estimate_food_budget(basic: dict) -> float | None:
    """根据总预算和出行人数估算人均餐饮预算上限。

    简单策略：总预算 ÷ 人数 ÷ 行程天数 ÷ 3（三餐），取整；
    拿不到时返回 None（不过滤）。
    """
    total_budget = basic.get("total_budget")
    if not total_budget:
        return None
    total = parse_price(total_budget)
    if total is None or total <= 0:
        return None

    travelers = 1
    travelers_raw = basic.get("travelers") or ""
    m = re.search(r"(\d+)", str(travelers_raw))
    if m:
        travelers = max(1, int(m.group(1)))

    # 经验值：餐饮占总预算约 25%，人均每餐上限 = 总预算×0.25 ÷ 人数 ÷ 6（按2天×3餐估算）
    per_meal = total * 0.25 / travelers / 6
    return round(per_meal, 0)


@server.tool(description="综合搜索某目的地（天气+酒店+景点+餐厅+促销+活动），返回结构化 JSON。")
def search_trip(destination: str, start_date: str, end_date: str | None = None) -> str:
    return json.dumps(
        run_search({"destination": destination, "start_date": start_date, "end_date": end_date}),
        ensure_ascii=False,
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
