"""路线补全：用高德 Web 服务 API 给 blocks 补坐标，并计算相邻地点间的真实路线。

- 地理编码：/v5/place/text 按「城市 + 名称」查 POI，拿到 GCJ-02 坐标
- 路线：按直线距离选择 步行 / 公交地铁 / 驾车，返回距离、耗时、折线
- 结果缓存在 route_cache.json，同名地点和同一段路线不重复请求
未配置 AMAP_KEY 或请求失败时静默跳过，不影响计划生成。
"""

import json
import math
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import certifi
except ImportError:  # 未安装时退化为系统证书库，不影响其余规划流程
    certifi = None

AMAP_BASE = "https://restapi.amap.com"
CACHE_PATH = Path(__file__).resolve().parent / "route_cache.json"
WALK_MAX_KM = 1.2
TRANSIT_MAX_KM = 25
# 高德个人开发者 key 的 QPS 较低（约 1~3），串行 + 限速避免触发限流重试
MAX_WORKERS = 1
REQUEST_GAP = 0.25  # 每次请求间隔（秒），控制到约 4 QPS 以内

_lock = threading.Lock()
_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cache = {}
        _cache.setdefault("geo", {})
        _cache.setdefault("leg", {})
    return _cache


def _save_cache() -> None:
    with _lock:
        CACHE_PATH.write_text(json.dumps(_load_cache(), ensure_ascii=False), encoding="utf-8")


def _get(path: str, **params) -> dict:
    params["key"] = os.getenv("AMAP_KEY", "")
    url = f"{AMAP_BASE}{path}?{urllib.parse.urlencode(params)}"
    # certifi 可用时用它的 CA 包（兼容 macOS/部分 Windows），否则用系统证书库
    context = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    time.sleep(REQUEST_GAP)
    for attempt in range(2):
        with urllib.request.urlopen(url, timeout=10, context=context) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "1":
            return data
        # 10021/10019/10020：QPS 超限，稍等重试一次
        if data.get("infocode") in ("10019", "10020", "10021") and attempt == 0:
            time.sleep(0.5)
            continue
        raise RuntimeError(f"amap {path}: {data.get('info')} ({data.get('infocode')})")
    return {}


def _km(a: str, b: str) -> float:
    x1, y1 = map(float, a.split(","))
    x2, y2 = map(float, b.split(","))
    dx = math.radians(x2 - x1) * math.cos(math.radians((y1 + y2) / 2))
    dy = math.radians(y2 - y1)
    return 6371 * math.hypot(dx, dy)


def _points(raw) -> list[list[float]]:
    """高德折线 "lng,lat;lng,lat" → [[lng, lat], ...]；v5 公交里折线可能包在 {"polyline": ...} 里。"""
    if isinstance(raw, dict):
        raw = raw.get("polyline")
    if not isinstance(raw, str):
        return []
    pts = []
    for pair in raw.split(";"):
        if "," in pair:
            lng, lat = pair.split(",")[:2]
            pts.append([round(float(lng), 5), round(float(lat), 5)])
    return pts


def geocode(name: str, city: str) -> dict | None:
    cache = _load_cache()["geo"]
    key = f"{city}|{name}"
    if key in cache:
        return cache[key]
    try:
        pois = _get(
            "/v5/place/text", keywords=name, region=city, city_limit="true", page_size=1
        ).get("pois") or []
    except Exception:  # noqa: BLE001
        return None  # 网络/配额错误不写缓存，下次再试
    hit = None
    if pois and pois[0].get("location"):
        p = pois[0]
        hit = {"location": p["location"], "poi_id": p.get("id") or "", "citycode": p.get("citycode") or ""}
    with _lock:
        cache[key] = hit
    return hit


def _walk_or_drive(path: str, origin: str, dest: str, mode: str) -> dict:
    route = _get(path, origin=origin, destination=dest, show_fields="cost,polyline")["route"]
    p = route["paths"][0]
    return {
        "mode": mode,
        "distance_m": int(float(p.get("distance") or 0)),
        "duration_s": int(float((p.get("cost") or {}).get("duration") or 0)),
        "lines": [],
        "polyline": [pt for s in p.get("steps") or [] for pt in _points(s.get("polyline"))],
    }


def _transit(origin: str, dest: str, citycode: str) -> dict | None:
    route = _get(
        "/v5/direction/transit/integrated",
        origin=origin,
        destination=dest,
        city1=citycode,
        city2=citycode,
        show_fields="cost,polyline",
    ).get("route") or {}
    transits = route.get("transits") or []
    if not transits:
        return None
    t = transits[0]
    line: list[list[float]] = []
    names: list[str] = []
    for seg in t.get("segments") or []:
        for step in (seg.get("walking") or {}).get("steps") or []:
            line += _points(step.get("polyline"))
        for bus in ((seg.get("bus") or {}).get("buslines") or [])[:1]:
            names.append((bus.get("name") or "").split("(")[0])
            line += _points(bus.get("polyline"))
    return {
        "mode": "transit",
        "distance_m": int(float(t.get("distance") or 0)),
        "duration_s": int(float((t.get("cost") or {}).get("duration") or 0)),
        "lines": [n for n in names if n],
        "polyline": line,
    }


def route_leg(a: dict, b: dict) -> dict | None:
    cache = _load_cache()["leg"]
    key = f"{a['location']}>{b['location']}"
    if key in cache:
        return cache[key]
    km = _km(a["location"], b["location"])
    try:
        if km < WALK_MAX_KM:
            leg = _walk_or_drive("/v5/direction/walking", a["location"], b["location"], "walk")
        else:
            leg = None
            if km < TRANSIT_MAX_KM and a.get("citycode"):
                try:
                    leg = _transit(a["location"], b["location"], a["citycode"])
                except Exception:  # noqa: BLE001
                    leg = None
            if leg is None:
                leg = _walk_or_drive("/v5/direction/driving", a["location"], b["location"], "drive")
    except Exception:  # noqa: BLE001
        return None
    with _lock:
        cache[key] = leg
    return leg


def _is_stop(block: dict) -> bool:
    # 去程/回程的航班车次、多选一的餐饮推荐无法定位到一个点
    return bool(block.get("name")) and block.get("type") != "交通" and block.get("note") != "餐饮推荐"


def geocode_blocks(blocks: list[dict], city: str) -> None:
    """原地给可定位的 block 写入 lng/lat/poi_id。"""
    if not os.getenv("AMAP_KEY") or not city:
        return
    stops = [b for b in blocks if _is_stop(b)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        hits = list(ex.map(lambda b: geocode(b["name"], city), stops))
    for b, hit in zip(stops, hits):
        if hit:
            lng, lat = hit["location"].split(",")
            b["lng"], b["lat"] = float(lng), float(lat)
            b["poi_id"] = hit["poi_id"]
            b["_geo"] = hit
    _save_cache()


def attach_routes(result: dict, city: str) -> dict:
    """给 result["blocks"] 补坐标，并生成 result["legs"]：每个方案每天按顺序相邻地点间的路线。

    第 N 天从第 N-1 天的酒店出发，当天最后回到当天酒店。
    """
    blocks = result.get("blocks") or []
    if not os.getenv("AMAP_KEY") or not city or not blocks:
        return result
    geocode_blocks(blocks, city)

    # 按 (方案, 天) 分组，保持 blocks 原有顺序（schedule → 酒店）
    groups: dict[tuple, list[dict]] = {}
    for b in blocks:
        if b.get("_geo"):
            groups.setdefault((b.get("plan_style") or "", b.get("day")), []).append(b)

    pairs: list[tuple[str, int, dict, dict]] = []
    last_hotel: dict[str, dict] = {}
    for (style, day), stops in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        seq = list(stops)
        prev_hotel = last_hotel.get(style)
        if prev_hotel and seq and seq[0]["_geo"]["location"] != prev_hotel["_geo"]["location"]:
            seq.insert(0, prev_hotel)
        for a, b in zip(seq, seq[1:]):
            if a["_geo"]["location"] != b["_geo"]["location"]:
                pairs.append((style, day, a, b))
        hotels = [s for s in stops if s.get("type") == "酒店"]
        if hotels:
            last_hotel[style] = hotels[-1]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        routed = list(ex.map(lambda p: route_leg(p[2]["_geo"], p[3]["_geo"]), pairs))
    _save_cache()

    legs = []
    for (style, day, a, b), leg in zip(pairs, routed):
        if leg:
            legs.append({"plan_style": style, "day": day, "from": a["id"], "to": b["id"], **leg})
    for b in blocks:
        b.pop("_geo", None)
    result["legs"] = legs
    return result


def strip_geo(blocks: list[dict]) -> None:
    for b in blocks:
        b.pop("_geo", None)
