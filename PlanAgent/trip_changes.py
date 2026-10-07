"""Resolve explicit trip-parameter edits without allowing the planner to invent them."""

import copy
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


_DIGITS = {char: value for value, char in enumerate("零一二三四五六七八九")}
_DIGITS.update({"两": 2, "〇": 0})
_QUANTITY = r"[-－]?(?:\d+(?:\.\d+)?[万千]?|[零〇一二三四五六七八九十百千万两]+)"
_DAY_QUANTITY = r"(?:\d+|[零〇一二三四五六七八九十百两]+)"
_DATE = re.compile(r"(?P<iso>\d{4}[-/]\d{1,2}[-/]\d{1,2})|(?:(?P<year>\d{4})年)?(?P<month>" + _DAY_QUANTITY + r")月(?P<day>" + _DAY_QUANTITY + r")(?:日|号)?")
_RELATIVE_DATE = re.compile(r"今天|明天|后天|(?:(?:下下周|下周|本周|这周|周)[一二三四五六日天末])|周末")


def _number(value: str) -> float:
    value = value.strip()
    if value.startswith(("-", "－")):
        raise ValueError("预算和出行人数必须大于零")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([万千]?)", value)
    if match:
        return float(match[1]) * {"": 1, "千": 1000, "万": 10000}[match[2]]
    total = section = number = 0
    last_unit = 1
    for character in value:
        if character in _DIGITS:
            number = _DIGITS[character]
            continue
        unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}.get(character)
        if unit is None:
            raise ValueError("无法识别这个数值，请填写具体数字")
        if unit == 10000:
            total += (section + number) * unit
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
        last_unit = unit
    # 常用预算写法“三千五”“一万五”分别表示3500和15000。
    if len(value) > 1 and value[-1] in _DIGITS and value[-2] in "百千万" and last_unit >= 100:
        number *= last_unit // 10
    return float(total + section + number)


def _date_value(match: re.Match, previous_start: date, today: date) -> date:
    if match.group("iso"):
        return date(*map(int, re.split(r"[-/]", match.group("iso"))))
    year = int(match.group("year") or max(previous_start.year, today.year))
    result = date(year, int(_number(match.group("month"))), int(_number(match.group("day"))))
    if not match.group("year") and result < today:
        result = date(year + 1, result.month, result.day)
    return result


def _date_field(text: str, start: int, end: int, labels: list) -> str | None:
    nearby = [(min(abs(label.start() - end), abs(start - label.end())), label[0])
              for label in labels if min(abs(label.start() - end), abs(start - label.end())) <= 12]
    if nearby:
        label = min(nearby, key=lambda pair: pair[0])[1]
        return "end_date" if label in ("返程", "回程", "返回", "回来", "结束日期") else "start_date"
    if re.search(r"改为|改到|改成|调整到|推迟到|提前到", text[max(0, start - 8):start]):
        return "start_date"
    return None


def _relative_date_value(value: str, today: date) -> date:
    if value in ("今天", "明天", "后天"):
        return today + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[value])
    week = 2 if value.startswith("下下周") else 1 if value.startswith("下周") else 0
    weekday = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}[value[-1]]
    result = today - timedelta(days=today.weekday()) + timedelta(days=week * 7 + weekday)
    if result < today and value in ("周末", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天"):
        result += timedelta(days=7)
    return result


def resolve_trip_changes(instruction: str, basic: dict | None, start_date: str,
                         end_date: str, today: date | None = None) -> dict:
    """Only change parameters stated in the instruction; preserve every other field."""
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        original_start, original_end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (ValueError, TypeError):
        raise ValueError("原计划缺少有效日期，请先确认出发和返程日期") from None
    if original_end < original_start:
        raise ValueError("结束日期不能早于出发日期")
    start, end = original_start, original_end
    days = (end - start).days + 1
    result_basic = copy.deepcopy(basic or {})
    updates = {}
    text = str(instruction or "").strip()

    budget_pattern = re.compile(r"(人均预算|每人预算|总预算|预算总额|总花费|预算)([^\d零〇一二三四五六七八九十百千万两，。；\n\-－]{0,12})(不限|不设限|无上限|" + _QUANTITY + r")")
    for match in budget_pattern.finditer(text):
        label, bridge, amount_text = match.groups()
        before = text[max(0, match.start() - 6):match.start()]
        if label == "预算" and re.search(r"餐厅|餐饮|饭店|酒店|住宿|门票|交通|每天|每顿", before):
            continue
        if label == "预算" and re.search(r"人均|每人", bridge):
            label = "人均预算"
        remainder = re.split(r"[，。；\n]", text[match.end():], maxsplit=1)[0]
        changed_amount = re.match(r"(?:元)?(?:降到|降为|降低到|降低为|改为|调整为|提高到|增加到)(" + _QUANTITY + r")", remainder)
        if changed_amount:
            amount_text = changed_amount[1]
        if amount_text in ("不限", "不设限", "无上限"):
            result_basic.pop("total_budget", None)
            result_basic.pop("budget_per_person", None)
            result_basic.update(budget_unlimited=True, budget_mode="unlimited", budget_tiers=["不设限"])
        else:
            amount = _number(amount_text)
            if amount <= 0:
                raise ValueError("预算必须大于零，也可以明确填写预算不限")
            result_basic.pop("budget_unlimited", None)
            if result_basic.get("budget_tiers") == ["不设限"]:
                result_basic.pop("budget_tiers", None)
            if label in ("人均预算", "每人预算"):
                travelers = re.search(r"\d+", str(result_basic.get("travelers") or ""))
                if not travelers:
                    raise ValueError("调整人均预算前，请先补充出行人数")
                result_basic.update(budget_per_person=amount, total_budget=amount * int(travelers[0]), budget_mode="per_person")
            else:
                result_basic.pop("budget_per_person", None)
                result_basic.update(total_budget=amount, budget_mode="total")
        updates["budget"] = True

    person = re.search(r"(?:出行人数|同行人数|人数)[^\d零〇一二三四五六七八九十百两，。；\n]{0,8}(" + _DAY_QUANTITY + r")(?:个人|人|位)", text)
    if person:
        count = int(_number(person[1]))
        if not 1 <= count <= 100:
            raise ValueError("请填写1至100人的出行人数")
        result_basic["travelers"] = f"{count}人"
        if result_basic.get("budget_mode") == "per_person" and result_basic.get("budget_per_person"):
            result_basic["total_budget"] = float(result_basic["budget_per_person"]) * count
        updates["travelers"] = True

    explicit_dates = {}
    labels = list(re.finditer(r"出发|启程|动身|出行日期|开始日期|返程|回程|返回|回来|结束日期", text))
    for match in _DATE.finditer(text):
        field = _date_field(text, match.start(), match.end(), labels)
        if not field:
            continue
        try:
            parsed = _date_value(match, original_start, today)
        except ValueError:
            raise ValueError("日期无效，请填写实际存在的年月日") from None
        if parsed < today:
            raise ValueError("新的行程日期不能早于今天")
        explicit_dates[field] = parsed
    for match in _RELATIVE_DATE.finditer(text):
        field = _date_field(text, match.start(), match.end(), labels)
        if not field:
            continue
        parsed = _relative_date_value(match[0], today)
        if parsed < today:
            raise ValueError("这个日期已过去，请给出未来的出发或返程日期")
        explicit_dates[field] = parsed
    calendar_edit = re.search(
        r"(?:出发(?:日期)?|启程|动身|返程|回程|行程日期|日期)[^，。；\n]{0,8}(?:改(?:为|到|成|一下)?|调整|推迟|提前|换成|更改)"
        r"|(?:改为|改到|调整到|推迟到|提前到|换成)[^，。；\n]{0,12}(?:出发|启程|返程|回程|日期)", text)
    if calendar_edit and not explicit_dates and not re.search(r"不要|不用|无需|别", calendar_edit[0]):
        raise ValueError("请补充具体出发或返程日期，例如10月20日、明天或下周五")

    duration = None
    absolute = re.search(r"(?:玩|待|行程(?:改为|改成|调整为)?|旅行(?:改为|改成)?|改为|改成)(" + _DAY_QUANTITY + r")天", text)
    if absolute:
        duration = int(_number(absolute[1]))
    relative = re.search(r"(多玩|多待|再玩|延长(?:行程)?|增加(?:行程)?|加|少玩|少待|缩短(?:行程)?|减少(?:行程)?)(" + _DAY_QUANTITY + r")天", text)
    if relative:
        adjustment = int(_number(relative[2]))
        duration = days + (-adjustment if relative[1].startswith(("少", "缩短", "减少")) else adjustment)
    if duration is not None and not 1 <= duration <= 60:
        raise ValueError("行程长度需要在1至60天之间")
    if "start_date" in explicit_dates:
        start = explicit_dates["start_date"]
    if "end_date" in explicit_dates:
        end = explicit_dates["end_date"]
        if end < start:
            raise ValueError("返程日期不能早于出发日期")
        explicit_days = (end - start).days + 1
        if duration is not None and duration != explicit_days:
            raise ValueError("游玩天数与出发、返程日期不一致，请确认希望玩几天")
        days = explicit_days
    elif duration is not None or "start_date" in explicit_dates:
        days = duration if duration is not None else days
        end = start + timedelta(days=days - 1)
    if not 1 <= days <= 60:
        raise ValueError("行程长度需要在1至60天之间")
    result_basic["duration_days"] = days
    if start != original_start or end != original_end:
        updates["dates"] = True
    return {"basic": result_basic, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "days": days, "updates": updates}
