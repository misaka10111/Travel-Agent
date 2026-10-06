"""行为信号 → 长期偏好 的规则聚合器。

把 BehaviorSignal 原始流水归纳成 UserPreference.preferences。
规则是可解释的确定性规则：同类正向/负向信号达到阈值后才升级为偏好。
"""

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BehaviorSignal, UserPreference

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_env import component_python, component_script, subprocess_env  # noqa: E402

CONVERSATION_SUMMARY_PY = component_script("PlanAgent", "conversation_summary.py")
PLAN_PYTHON = component_python("PlanAgent")


def _subprocess_env() -> dict:
    """子进程环境：UTF-8 标准流 + 清理 macOS venv 遗留变量。"""
    return subprocess_env()

POSITIVE_ACTIONS = {
    "add",
    "like",
    "keep",
    "prefer",
    "select",
    "confirm",
}
NEGATIVE_ACTIONS = {
    "remove",
    "dislike",
    "skip",
    "drop",
    "avoid",
    "delete",
}

PACE_WORDS = {
    "轻松": ["轻松", "休闲", "慢节奏", "不赶", "宽松"],
    "紧凑": ["紧凑", "充实", "多玩", "快节奏", "排满"],
}
TRANSPORT_WORDS = {
    "打车优先": ["打车", "出租车", "网约车", "taxi"],
    "地铁优先": ["地铁", "轨道交通", "subway", "metro"],
    "公交优先": ["公交", "巴士", "bus"],
    "自驾优先": ["自驾", "租车", "开车"],
}
HOTEL_MUST = ["含早", "早餐", "近地铁", "高楼层", "安静", "有窗", "湖景", "海景"]
HOTEL_AVOID = ["无窗", "临街", "隔音差", "太吵"]
FOOD_AVOID = ["不吃辣", "太辣", "忌口", "清真", "素食"]


def _action_polarity(action: str) -> int:
    a = (action or "").strip().lower()
    if a in POSITIVE_ACTIONS or any(k in a for k in ("add", "like", "keep", "prefer", "select", "confirm")):
        return 1
    if a in NEGATIVE_ACTIONS or any(k in a for k in ("remove", "dislike", "skip", "drop", "avoid", "delete")):
        return -1
    return 0


def _count_hits(signals: list[BehaviorSignal], keywords: list[str], polarity: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in signals:
        if _action_polarity(s.action) != polarity:
            continue
        text = f"{s.target} {s.detail}".lower()
        for kw in keywords:
            if kw.lower() in text:
                counts[kw] = counts.get(kw, 0) + 1
    return counts


def _preferred(signals: list[BehaviorSignal], groups: dict[str, list[str]]) -> list[str]:
    """从多组关键词中选出累计出现次数最多的一组，平票取先出现者。"""
    scores: list[tuple[int, int, str]] = []
    for idx, (label, words) in enumerate(groups.items()):
        score = 0
        for s in signals:
            text = f"{s.target} {s.detail}".lower()
            if any(w.lower() in text for w in words):
                score += 1
        scores.append((score, -idx, label))
    scores.sort(reverse=True)
    if not scores or scores[0][0] == 0:
        return []
    return [scores[0][2]]


def _learn(signals: list[BehaviorSignal], min_count: int) -> dict:
    prefs: dict = {}

    # 节奏与交通：直接取出现最多的偏好
    pace = _preferred(signals, PACE_WORDS)
    transport = _preferred(signals, TRANSPORT_WORDS)
    if pace:
        prefs["pace"] = pace[0]
    if transport:
        prefs["transport"] = transport

    # 酒店硬性要求 / 避雷
    hotel_must = [k for k, c in _count_hits(signals, HOTEL_MUST, 1).items() if c >= min_count]
    hotel_avoid = [k for k, c in _count_hits(signals, HOTEL_AVOID, -1).items() if c >= min_count]
    food_avoid = [k for k, c in _count_hits(signals, FOOD_AVOID, -1).items() if c >= min_count]
    if hotel_must:
        prefs.setdefault("hotel", {})["must"] = hotel_must
    if hotel_avoid:
        prefs.setdefault("hotel", {})["avoid"] = hotel_avoid
    if food_avoid:
        prefs.setdefault("food", {})["avoid"] = food_avoid

    # 喜欢/不喜欢的具体对象（景点、饭店、酒店等），出现次数达到阈值才沉淀
    liked: dict[str, int] = {}
    disliked: dict[str, int] = {}
    for s in signals:
        target = (s.target or "").strip()
        if not target:
            continue
        polarity = _action_polarity(s.action)
        if polarity > 0:
            liked[target] = liked.get(target, 0) + 1
        elif polarity < 0:
            disliked[target] = disliked.get(target, 0) + 1
    liked = {k: v for k, v in liked.items() if v >= min_count}
    disliked = {k: v for k, v in disliked.items() if v >= min_count}
    if liked:
        prefs["liked"] = sorted(liked, key=lambda k: -liked[k])
    if disliked:
        prefs["avoided"] = sorted(disliked, key=lambda k: -disliked[k])

    return prefs


def _merge(base: dict, learned: dict) -> dict:
    """把本次学到的偏好合并进已有偏好，保留人工设置的内容。"""
    out = deepcopy(base or {})
    for key, value in learned.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            for sub, sub_value in value.items():
                if isinstance(sub_value, list) and isinstance(out[key].get(sub), list):
                    merged = list(out[key][sub])
                    for item in sub_value:
                        if item not in merged:
                            merged.append(item)
                    out[key][sub] = merged
                else:
                    out[key][sub] = sub_value
        elif isinstance(value, list) and isinstance(out.get(key), list):
            merged = list(out[key])
            for item in value:
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        else:
            out[key] = value
    return out


def aggregate_preferences(user_id: str, db: Session, min_count: int = 3) -> UserPreference:
    """读取该用户的行为信号，聚合偏好并 upsert 到 UserPreference。"""
    signals = list(
        db.scalars(
            select(BehaviorSignal)
            .where(BehaviorSignal.user_id == user_id)
            .order_by(BehaviorSignal.created_at.asc())
        )
    )
    current = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    learned = _learn(signals, min_count)
    merged = _merge(current.preferences if current else {}, learned)

    if current is None:
        current = UserPreference(user_id=user_id, preferences=merged)
        db.add(current)
    else:
        current.preferences = merged
    db.commit()
    db.refresh(current)
    return current


def _summarize_conversation(conversation: list) -> dict:
    """调用 PlanAgent venv 里的轻量模型，把用户对话提炼成偏好 JSON。"""
    if not conversation:
        return {}
    try:
        proc = subprocess.run(
            [str(PLAN_PYTHON), str(CONVERSATION_SUMMARY_PY)],
            input=json.dumps({"conversation": conversation}, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=_subprocess_env(),
        )
        data = json.loads(proc.stdout)
    except Exception:
        return {}
    prefs = data.get("preferences") if isinstance(data, dict) else None
    return prefs if isinstance(prefs, dict) else {}


def merge_conversation_preferences(
    user_id: str, conversation: list, db: Session
) -> UserPreference | None:
    """从对话中提取偏好并合并到 UserPreference，失败时返回 None。"""
    learned = _summarize_conversation(conversation)
    if not learned:
        return None
    current = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    merged = _merge(current.preferences if current else {}, learned)
    if current is None:
        current = UserPreference(user_id=user_id, preferences=merged)
        db.add(current)
    else:
        current.preferences = merged
    db.commit()
    db.refresh(current)
    return current
