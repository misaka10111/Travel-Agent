# 餐饮模块对接

千问 `qwen-max` 解析餐饮需求，高德提供餐厅事实。现有前端已接入 `food_options`，生成与局部修改行程时自动传给后端；餐饮块展示人均价格、评分、菜系、地址与高德详情链接。

## 启动与配置

完整行程沿用项目的后端 `POST /api/plan`（默认端口 `8000`），无需另启餐饮服务；其他智能体仍需各自的依赖与配置。餐饮配置放在后端的 `SearchAgent/.env`，不要提交真实密钥或放入前端 `VITE_*`：

```dotenv
DASHSCOPE_API_KEY=你的千问密钥
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-max
AMAP_API_KEY=你的高德Web服务密钥
```

只测试餐饮时，在项目根目录启动独立服务：

```bash
python3 -m venv SearchAgent/.venv
source SearchAgent/.venv/bin/activate
python -m pip install -r SearchAgent/requirements-food.txt
python -m uvicorn SearchAgent.food_api:app --host 127.0.0.1 --port 8001
```

独立接口为 `POST http://127.0.0.1:8001/api/search/food`，在线契约见 `/docs`；`GET /health` 仅检查服务进程。跨域来源默认允许本机 `5173`，可在 `.env` 设置 `FOOD_CORS_ORIGINS=["https://你的前端域名"]` 后重启。

## 输入字段

整体 `POST /api/plan` 在现有请求中附上 `food_options`：

```json
{
  "destination": "杭州",
  "start_date": "2026-10-10",
  "end_date": "2026-10-12",
  "food_options": {
    "query": "想吃杭帮菜",
    "max_price_per_person": 100,
    "limit": 10
  }
}
```

独立餐饮接口直接发送 `{"destination":"杭州","query":"想吃杭帮菜，人均100元以内"}`，不套 `food_options`。请求均为 JSON，不能传入未定义字段。

| 字段 | 约定 |
| --- | --- |
| `destination` | 城市；独立接口与 `query` 至少提供一项，整体接口使用顶层目的地 |
| `query` | 餐饮自然语言，最多 2000 字符；前端会拦截超长需求或修改历史并提示缩短 |
| `cuisines` / `keywords` | 字符串数组，各最多 6 项，例如 `["川菜"]` / `["火锅"]` |
| `max_price_per_person` | 正数或 `null`，人民币/人/餐，不能填旅行总预算 |
| `min_rating` | `0..5` 或 `null` |
| `location` | `{longitude, latitude, coordinate_system: "GCJ-02"}`；浏览器 WGS84 坐标须先转换 |
| `radius_m` | 周边整数米，默认 `3000`，范围 `1..50000`；需有 `location` |
| `limit` | 默认 `10`，范围 `1..50`，实际候选可能更少 |
| `include_unknown_price` / `include_unknown_rating` | 默认 `false`；筛选时是否保留缺失值 |

结构化字段优先于自然语言；省略字段让千问提取，显式 `null` 或 `[]` 清除对应限制。只传 `query` 时应写明城市；不传 `query` 的结构化请求无需千问。用户画像放 `profile`，人数和全程预算放 `basic`。

现有前端首次查询使用用户原始输入作为餐饮需求；补充旅行总预算不填入餐费字段。局部修改携带原餐饮需求与历史修改，并标注本次指令优先。

## 返回与展示

完整行程和局部修改响应都附带 `search`；餐饮成功时包含 `search.food`（餐厅列表）、`search.food_meta`（来源、已解析条件、时间与 `warnings`）。独立接口对应顶层 `food`、`resolved_query`、`warnings`。

| 餐厅字段 | 含义 |
| --- | --- |
| `id` / `name` / `title` | 高德 POI ID、名称；`title` 兼容旧格式 |
| `address` / `location` | 地址、GCJ-02 经纬度 |
| `price_per_person` | 人均消费参考；`currency="CNY"`、`price_unit="person/meal"` |
| `rating` / `rating_scale` | 高德评分参考，满分 `5` |
| `cuisine` / `cuisine_source` | 高德分类或标签识别的菜系及来源 |
| `url` | 按高德 POI ID 生成的高德详情页链接，用于“查看餐厅详情” |
| `website` / `telephone` | 高德提供的官网、电话，可能缺失 |
| `distance_m` / `distance_kind` | 到检索中心的直线距离，单位米；不是路线距离 |
| `missing_fields` / `source` / `fetched_at` | 缺失标记、来源与检索时间 |

缺失值是 `null`，显示“未知”或“暂无”，不能当作 `0`；当前 `reviews`、`opening_hours` 无数据。价格、评分不是实时菜单或订座报价，菜系标签不能保证满足忌口、过敏等要求。没有匹配时 `food: []`，读取提示说明原因。

PlanAgent 接收完整 `search.food` 与提示，按返回餐厅回填行程的 `link`；前端直接使用餐厅 `url` 展示详情。餐厅匹配不到来源时，不猜测价格、评分或链接。前端 API 类型在 `frontend/src/api/types.ts`，调用在 `client.ts` 和 `pages/AgentPage.tsx`；独立调用示例见 [examples/food-client.ts](examples/food-client.ts)。

## 错误与单独联调

独立接口错误格式为 `{"error":{"code":"...","message":"...","details":[]}}`，展示 `error.message`：`422` 参数/解析错误，`503` 缺少配置，`502` 上游失败，`504` 超时。完整行程读取 `search.food_meta.warnings`；餐饮失败时 `search.food` 可能是 `{error: "..."}`，现有页面会显示该错误。空列表与请求失败需分别处理。

```bash
python -m SearchAgent.food_cli --file SearchAgent/examples/food_request.json
python -m SearchAgent.food_cli --query '杭州找川菜，人均80元以内'
python -m SearchAgent.food_cli --schema
```

CLI 成功退出码为 `0`，失败为 `1`；`--schema` 输出完整请求/响应字段。菜系、位置、人均价格、评分来自高德，千问不生成餐厅事实。
