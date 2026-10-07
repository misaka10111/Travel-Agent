#!/usr/bin/env python3
"""Explicit, paid live regression; never collected by pytest.

Run while the backend is listening: python3 scripts/verify_live_flow.py
This uses the configured Qwen/AMap services through localhost, without a user ID
or persistent trip creation. Results are saved outside the repository.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def redact(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if re.search(r"api.?key|authorization|secret|token", key, re.I) and key != "max_tokens" else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"sk-[A-Za-z0-9_.-]+", "[redacted]", value)
        return re.sub(r"(?i)([?&](?:key|api_key|apikey)=)[^&\s]+", r"\1[redacted]", value)
    return value


def configured_models():
    models = {}
    for module in ("PlanAgent", "SearchAgent", "ValidateAgent"):
        match = re.search(r"(?m)^\s*OPENAI_MODEL\s*=\s*([^\r\n#]+)", (ROOT / module / ".env").read_text())
        models[module] = match[1].strip().strip("\"'") if match else "unset"
    return models


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def coord(item):
    lng, lat = number(item.get("lng", item.get("longitude"))), number(item.get("lat", item.get("latitude")))
    return [lng, lat] if lng is not None and lat is not None and 70 < lng < 140 and 15 < lat < 55 else None


def distance(a, b):
    x1, y1 = a
    x2, y2 = b
    return 6371000 * math.hypot(math.radians(x2 - x1) * math.cos(math.radians((y1 + y2) / 2)), math.radians(y2 - y1))


def span(item):
    match = re.fullmatch(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", str(item.get("time") or ""))
    if match:
        h1, m1, h2, m2 = map(int, match.groups())
        return h1 * 60 + m1, h2 * 60 + m2
    return None


class LiveFlow:
    def __init__(self, args):
        self.args = args
        self.saved_run = json.loads(Path(args.resume_plan).read_text()) if args.resume_plan else None
        self.report = {"started_at": datetime.now().isoformat(), "models": configured_models(), "stages": [], "checks": {}}
        self.save()

    def save(self):
        path = Path(self.args.artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(redact(self.report), ensure_ascii=False, indent=2))

    def check(self, name, passed, detail=None):
        self.report["checks"][name] = {"passed": bool(passed), "detail": redact(detail)}
        self.save()
        print(json.dumps({"check": name, "passed": bool(passed), "detail": redact(detail)}, ensure_ascii=False), flush=True)

    def post(self, route, payload, name, streaming=False):
        started = time.monotonic()
        request = urllib.request.Request(self.args.base_url.rstrip("/") + route,
            data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=650) as response:
                if streaming:
                    final, events, last = None, [], started
                    for raw in response:
                        line = raw.decode().strip()
                        if not line.startswith("data:"):
                            continue
                        raw_event = line[5:].strip()
                        if raw_event == "[DONE]":
                            break
                        event = json.loads(raw_event)
                        events.append(event)
                        if event.get("type") == "node":
                            now = time.monotonic()
                            print(json.dumps({"stage": event.get("node"), "seconds_since_previous": round(now - last, 2), "elapsed": round(now - started, 2)}, ensure_ascii=False), flush=True)
                            last = now
                        if event.get("type") == "final":
                            final = event.get("data")
                    self.report["stream_events"] = events
                    if not isinstance(final, dict):
                        raise RuntimeError("SSE未返回完整计划；事件详情已保存在结果文件")
                    result = final
                else:
                    result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", "HTTP请求失败")
            except Exception:
                detail = "HTTP请求失败"
            raise RuntimeError(f"{name}: HTTP {exc.code}: {redact(detail)}") from None
        elapsed = round(time.monotonic() - started, 2)
        self.report[name] = result
        self.report["stages"].append({"name": name, "seconds": elapsed})
        self.save()
        print(json.dumps({"stage": name, "seconds": elapsed, "error": redact(result.get("error"))}, ensure_ascii=False), flush=True)
        if result.get("error"):
            raise RuntimeError(f"{name} returned an error; sanitized details in artifact")
        return result

    def inspect_plan(self, plan, label):
        blocks, legs = plan.get("blocks") or [], plan.get("legs") or []
        ids = {block.get("id") for block in blocks}
        self.check(label + "_route_ids", bool(legs) and all(leg.get("from") in ids and leg.get("to") in ids for leg in legs), {"blocks": len(blocks), "legs": len(legs)})
        self.check(label + "_stable_unique_ids", len(ids) == len(blocks) and None not in ids)
        days = {}
        for block in blocks:
            if block.get("type") != "酒店" and span(block):
                days.setdefault((block.get("plan_style"), block.get("day")), []).append(block)
        valid_windows, no_overlap = True, True
        for items in days.values():
            ordered = sorted(items, key=lambda block: span(block)[0])
            no_overlap &= all(span(a)[1] <= span(b)[0] for a, b in zip(ordered, ordered[1:]))
            for block in items:
                window = block.get("activity_window")
                if block.get("type") != "交通" and isinstance(window, dict):
                    valid_windows &= window["start_min"] <= span(block)[0] < span(block)[1] <= window["end_min"]
        self.check(label + "_time_windows", valid_windows)
        self.check(label + "_no_overlap", no_overlap)
        short_transfers = []
        by_id = {block["id"]: block for block in blocks}
        for leg in legs:
            a, b = by_id.get(leg.get("from")), by_id.get(leg.get("to"))
            if not a or not b or a.get("day") != b.get("day") or a.get("type") in ("交通", "酒店") or b.get("type") in ("交通", "酒店"):
                continue
            if span(a) and span(b) and number(leg.get("duration_s")) is not None:
                gap = span(b)[0] - span(a)[1]
                required = math.ceil(number(leg["duration_s"]) / 60) + 5
                if gap < required:
                    short_transfers.append({"from": a["name"], "to": b["name"], "gap_minutes": gap, "required_minutes": required})
        self.check(label + "_actual_transfer_time_fits", not short_transfers, short_transfers)
        transport = [block for block in blocks if block.get("type") == "交通"]
        selected = plan.get("selected_transport") or {}
        verified_transport = all(any(
            block.get("direction") == direction
            and (record.get("train_no") or "__missing__") in block.get("name", "")
            and block.get("dep_time") == record.get("dep_dt")
            and block.get("arr_time") == record.get("arr_dt")
            for block in transport
        ) for key, direction in (("outbound", "去"), ("inbound", "回")) for record in [selected.get(key) or {}])
        self.check(label + "_verified_train_timing", verified_transport)
        meals = [block for block in blocks if block.get("type") == "美食"]
        self.check(label + "_nearby_meal_links_coordinates", bool(meals) and all(coord(block) and block.get("link") and number(block.get("distance_m")) is not None and number(block["distance_m"]) <= 1500 for block in meals), [{"name": block.get("name"), "distance_m": block.get("distance_m"), "walking_distance_m": block.get("walking_distance_m"), "walking_duration_s": block.get("walking_duration_s"), "unit_price": block.get("unit_price")} for block in meals])
        walking_meals = [block for block in meals if number(block.get("walking_distance_m")) is not None]
        self.check(label + "_verified_walking_not_far", all(number(block["walking_distance_m"]) <= 5000 for block in walking_meals), {"verified": len(walking_meals), "total_meals": len(meals)})
        self.check(label + "_night_market_after_17", all(span(block) and span(block)[0] >= 17 * 60 for block in blocks if block.get("type") == "景点" and "夜市" in block.get("name", "")))
        west_lake = [block for block in blocks if block.get("name") == "西湖" and block.get("type") == "景点"]
        self.check(label + "_west_lake_does_not_inherit_restaurant_price", bool(west_lake) and all(block.get("price_source") == "unknown" or block.get("price_known") is False or number(block.get("unit_price")) == 0 for block in west_lake), [{"price_known": block.get("price_known"), "unit_price": block.get("unit_price"), "price_source": block.get("price_source")} for block in west_lake])

    def candidate_for_add(self, plan, search):
        candidates = []
        for meal in plan["blocks"]:
            if meal.get("type") != "美食":
                continue
            entries = [entry for entry in search.get("food_by_anchor") or [] if entry.get("day") == meal.get("day") and entry.get("meal") == meal.get("meal") and (not entry.get("plan_style") or entry["plan_style"] == meal.get("plan_style"))]
            for entry in entries:
                anchor = coord(entry)
                verified = [item for item in entry.get("restaurants") or [] if coord(item) and anchor and distance(coord(item), anchor) <= 1500 and (number(item.get("walking_distance_m")) is None or number(item["walking_distance_m"]) <= 1500) and number(item.get("price_per_person")) not in (None, 0) and (item.get("poi_detail_url") or item.get("map_url"))]
                for expensive in verified:
                    price = number(expensive["price_per_person"])
                    cheaper = [item for item in verified if item.get("name") != expensive.get("name") and number(item["price_per_person"]) < price]
                    if cheaper and expensive.get("name") != meal.get("name") and price <= 500:
                        candidates.append((price - min(number(item["price_per_person"]) for item in cheaper), meal, entry, expensive))
        if not candidates:
            raise RuntimeError("没有找到有真实报价、1.5公里内且有更便宜替代的餐厅，不能伪造回归样本")
        _, meal, entry, expensive = max(candidates, key=lambda candidate: candidate[0])
        return meal, entry, expensive

    def unchanged(self, before, after, selected, label):
        old = {block["id"]: block for block in before["blocks"] if block["id"] != selected}
        new = {block["id"]: block for block in after["blocks"] if block["id"] != selected}
        fields = ("id", "name", "time", "day", "plan_style")
        self.check(label, old.keys() == new.keys() and all(all(block.get(field) == new[key].get(field) for field in fields) for key, block in old.items()))

    def run(self):
        self.check("configured_qwen27b", all(model == self.args.expected_model for model in self.report["models"].values()), self.report["models"])
        if not self.report["checks"]["configured_qwen27b"]["passed"]:
            raise RuntimeError("模型配置不统一，终止付费测试")
        if self.args.resume_plan:
            saved = self.saved_run
            final, basic = saved["final"], saved["basic"]
            self.report.update(final=final, basic=basic, resumed_from=self.args.resume_plan)
        else:
            text = "从上海出发去杭州玩2天，2个人总预算4000元，想去西湖和浙江省博物馆，喜欢杭帮菜，想坐高铁，节奏轻松。"
            messages = [{"role": "user", "content": text}]
            ask = self.post("/question", {"messages": messages, "has_plan": False}, "question_missing_date")
            self.check("only_missing_date_is_asked", ask.get("action") == "ask" and ask.get("missing") == ["start_date"] and [q.get("field") for q in ask.get("questions") or []] == ["start_date"], {"action": ask.get("action"), "missing": ask.get("missing")})
            start = date.fromisoformat(self.args.start_date)
            end = start + timedelta(days=1)
            reply = f"{start.isoformat()}出发，{end.isoformat()}回上海。"
            messages += [{"role": "assistant", "content": ask.get("question") or "计划什么时间去？"}, {"role": "user", "content": reply}]
            complete = self.post("/question", {"messages": messages, "has_plan": False, "trip_data": ask.get("data") or {}}, "question_completed")
            basic = complete.get("data") or {}
            self.check("completed_trip_parameters", complete.get("action") in ("confirm_trip", "plan") and basic.get("destination") == "杭州" and basic.get("origin") == "上海" and basic.get("start_date") == start.isoformat() and basic.get("end_date") == end.isoformat() and number(basic.get("total_budget")) == 4000 and re.search(r"2", str(basic.get("travelers"))) is not None, {key: basic.get(key) for key in ("destination", "origin", "start_date", "end_date", "travelers", "total_budget", "food_keyword")})
            if not self.report["checks"]["completed_trip_parameters"]["passed"]:
                raise RuntimeError("自由输入参数识别失败，停止依赖此参数的规划")
            self.report["basic"] = basic
            payload = {"destination": basic["destination"], "start_date": basic["start_date"], "end_date": basic["end_date"], "basic": basic, "profile": {"travel_style": ["休闲度假"]}}
            final = self.post("/plan/stream", payload, "final", streaming=True)
        plan, search = final.get("plan") or {}, final.get("search") or {}
        if not plan.get("blocks"):
            raise RuntimeError("最终计划无有效行程块")
        audit = final.get("audit") or {}
        self.check("audit_returned_valid_conclusion", isinstance(audit.get("passed"), bool) and not audit.get("error"), audit)
        self.check("audit_passed", audit.get("passed") is True and not audit.get("error"), {"issue_count": len(audit.get("issues") or []), "feedback": audit.get("feedback"), "error": audit.get("error")})
        self.inspect_plan(plan, "initial")
        meal, anchor, candidate = self.candidate_for_add(plan, search)
        self.report["add_source_candidate"] = {"target_id": meal["id"], "anchor": anchor, "candidate": candidate}
        item = {"type": "美食", "time": meal["time"], "name": candidate["name"], "price": candidate["price_per_person"], "link": candidate.get("poi_detail_url") or candidate.get("map_url"), "lng": coord(candidate)[0], "lat": coord(candidate)[1], "poi_id": candidate.get("poi_id"), "distance_m": candidate.get("distance_m"), "cuisine": candidate.get("cuisine"), "rating": candidate.get("rating"), "address": candidate.get("address")}
        def mutation_payload(current, modify):
            return {"destination": current["destination"], "start_date": current["start_date"], "end_date": current["end_date"], "basic": basic, "plan": current, "search": search, "modify": {"plan_style": meal["plan_style"], "blocks": current["blocks"], "block_ids": [meal["id"]], **modify}}
        added = self.post("/plan", mutation_payload(plan, {"action": "add", "day": meal["day"], "item": item}), "added")
        added_meal = next(block for block in added["blocks"] if block["id"] == meal["id"])
        self.check("candidate_added_into_actual_plan", added_meal.get("name") == candidate["name"] and added_meal.get("link") == item["link"] and coord(added_meal) == coord(candidate) and number(added_meal.get("unit_price")) == number(candidate["price_per_person"]), {"name": added_meal.get("name"), "unit_price": added_meal.get("unit_price"), "price": added_meal.get("price"), "total_cost": added.get("total_cost")})
        self.unchanged(plan, added, meal["id"], "add_preserves_unselected_blocks")
        modified = self.post("/plan", mutation_payload(added, {"action": "modify", "mode": "block", "instruction": "把选中的这家餐厅换成更便宜的，能从相邻景点步行过去，距离最好1500米以内。保留这一餐原来的时间和其他计划。"}), "modified")
        changed = next(block for block in modified["blocks"] if block["id"] == meal["id"])
        self.unchanged(added, modified, meal["id"], "modify_preserves_unselected_blocks")
        self.check("selected_meal_became_cheaper", number(changed.get("unit_price")) is not None and number(changed["unit_price"]) < number(added_meal.get("unit_price")) and changed.get("name") != added_meal.get("name"), {"old": added_meal.get("name"), "old_unit_price": added_meal.get("unit_price"), "new": changed.get("name"), "new_unit_price": changed.get("unit_price")})
        matched = next((option for option in anchor.get("restaurants") or [] if option.get("name") == changed.get("name") and coord(option) == coord(changed)), None)
        self.check("changed_price_and_location_match_verified_source", bool(matched) and number(changed.get("unit_price")) == number((matched or {}).get("price_per_person")) and bool(changed.get("link")), {"distance_m": round(distance(coord(changed), coord(anchor))) if coord(changed) and coord(anchor) else None})
        expected_cost = number(added["total_cost"]) - number(added_meal["price"]) + number(changed["price"])
        self.check("actual_group_cost_recomputed", abs(number(modified.get("total_cost")) - expected_cost) < 0.02 and number(changed["price"]) == number(changed["unit_price"]) * 2, {"before": added.get("total_cost"), "after": modified.get("total_cost"), "expected": expected_cost})
        self.check("selected_meal_time_preserved", changed.get("time") == meal.get("time"))
        self.check("selected_meal_walking_requirement", number(changed.get("walking_distance_m")) is not None and number(changed["walking_distance_m"]) <= 1500, {"walking_distance_m": changed.get("walking_distance_m"), "walking_duration_s": changed.get("walking_duration_s")})
        previous_legs = {(leg["from"], leg["to"]): leg for leg in added.get("legs") or [] if meal["id"] in (leg["from"], leg["to"])}
        changed_legs = {(leg["from"], leg["to"]): leg for leg in modified.get("legs") or [] if meal["id"] in (leg["from"], leg["to"])}
        shared_pairs = previous_legs.keys() & changed_legs.keys()
        self.check("selected_meal_route_geometry_recomputed", bool(shared_pairs) and any(previous_legs[pair].get("polyline") != changed_legs[pair].get("polyline") for pair in shared_pairs), {"previous_connected_legs": len(previous_legs), "modified_connected_legs": len(changed_legs)})
        self.inspect_plan(modified, "modified")
        self.report["finished_at"] = datetime.now().isoformat()
        self.save()
        failed = [name for name, check in self.report["checks"].items() if not check["passed"]]
        print(json.dumps({"artifact": self.args.artifact, "checks": len(self.report["checks"]), "failed": failed, "stages": self.report["stages"]}, ensure_ascii=False), flush=True)
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api")
    parser.add_argument("--artifact", default="/private/tmp/travel-agent-live-flow.json")
    parser.add_argument("--start-date", default=(date.today() + timedelta(days=9)).isoformat())
    parser.add_argument("--expected-model", default="qwen3.8-27b")
    parser.add_argument("--resume-plan", help="Reuse final/basic from a prior artifact; only repeat mutations")
    args = parser.parse_args()
    flow = LiveFlow(args)
    try:
        return flow.run()
    except Exception as exc:
        flow.report["failure"] = {"type": type(exc).__name__, "message": redact(str(exc))}
        flow.save()
        print(json.dumps({"failure_type": type(exc).__name__, "message": redact(str(exc)), "artifact": args.artifact}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
