"""独立餐饮 CLI：JSON / 自然语言输入 → JSON 输出。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

if __package__:
    from .food import FoodSearchError, FoodSearchRequest, FoodSearchResponse, search_restaurants
else:
    # 同时支持 python SearchAgent/food_cli.py 和 python -m SearchAgent.food_cli。
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from SearchAgent.food import FoodSearchError, FoodSearchRequest, FoodSearchResponse, search_restaurants


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _error(code: str, message: str, details: list[dict] | None = None) -> int:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    _print_json({"error": error})
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="高德餐饮检索，结果为结构化 JSON")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--json", dest="json_input", help="JSON 请求对象")
    inputs.add_argument("--file", type=str, help="UTF-8 JSON / 文本文件；- 表示标准输入")
    inputs.add_argument("--query", help="自然语言餐饮需求，交给千问解析")
    parser.add_argument("--schema", action="store_true", help="输出请求和响应 JSON Schema")
    parser.add_argument("input", nargs="*", help="自然语言需求或 JSON 请求对象")
    args = parser.parse_args(argv)

    if args.schema:
        _print_json({
            "request": FoodSearchRequest.model_json_schema(),
            "response": FoodSearchResponse.model_json_schema(),
        })
        return 0

    if args.input and any(value is not None for value in (args.json_input, args.file, args.query)):
        return _error("invalid_request", "位置参数不能与 --json、--file 或 --query 同时使用。")

    try:
        if args.query is not None:
            data: Any = {"query": args.query}
        else:
            if args.json_input is not None:
                raw = args.json_input
            elif args.file is not None:
                raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            elif args.input:
                raw = " ".join(args.input)
            elif not sys.stdin.isatty():
                raw = sys.stdin.read()
            else:
                raw = ""
            raw = raw.strip()
            if not raw:
                return _error("invalid_request", "请输入 JSON 请求或自然语言餐饮需求。")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                if args.json_input is not None or raw.startswith(("{", "[")):
                    return _error("invalid_request", "JSON 格式不合法。")
                data = {"query": raw}
        if not isinstance(data, dict):
            return _error("invalid_request", "JSON 请求必须是对象。")
        request = FoodSearchRequest.model_validate(data)
        result = search_restaurants(request)
        _print_json(result.model_dump(mode="json"))
        return 0
    except ValidationError as exc:
        details = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _error("invalid_request", "餐饮查询参数不合法。", details)
    except FoodSearchError as exc:
        return _error(exc.code, exc.message)
    except (OSError, UnicodeError):
        return _error("invalid_request", "无法读取输入文件，请检查路径和 UTF-8 编码。")
    except Exception:
        # 网络客户端的原始异常可能包含请求 URL 和 API key。
        return _error("internal_error", "餐饮检索服务内部错误。")


if __name__ == "__main__":
    raise SystemExit(main())
