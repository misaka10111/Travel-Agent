# 餐饮 SearchAgent：前端与 PlanAgent 对接

餐饮模块接收用户的餐饮查询，使用千问 `qwen-max` 提取目的地、菜系、人均预算等查询条件，再从高德 Web 服务检索餐厅。返回值是带来源和缺失标记的餐厅事实列表，供 PlanAgent 根据用户画像、其他推荐地点及距离安排用餐。

本模块提供独立 HTTP 服务和 CLI，也接入现有 SearchAgent 的 `food` 输出。前端页面、用户画像处理和行程规划由各自模块负责。千问只解析查询条件，不生成餐厅、价格或评分。

## 1. 启动独立餐饮服务

在项目根目录运行：

```bash
python3 -m venv SearchAgent/.venv
source SearchAgent/.venv/bin/activate
python -m pip install -r SearchAgent/requirements-food.txt
```

如果还没有 `SearchAgent/.env`，复制 `SearchAgent/.env.example`；已有文件则直接补充以下配置：

```dotenv
DASHSCOPE_API_KEY=填写你的千问密钥
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-max
AMAP_API_KEY=填写你的高德Web服务密钥
FOOD_CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
```

这组配置不替换其他模块的 `OPENAI_*` 配置。高德 key 必须支持 Web 服务 API。真实密钥只放后端 `.env`；前端请求不携带密钥，也不要把密钥放进 `VITE_*` 变量。只有自然语言查询需要千问；不提供 `query` 的完整结构化请求可直接检索高德。

```bash
python -m uvicorn SearchAgent.food_api:app --host 127.0.0.1 --port 8001
```

| 用途 | 地址 |
| --- | --- |
| 查询餐厅 | `POST http://127.0.0.1:8001/api/search/food` |
| 服务存活检查 | `GET http://127.0.0.1:8001/health` |
| 在线接口文档 | `http://127.0.0.1:8001/docs` |

`/health` 返回 `{"status":"ok","service":"food-search"}`，表示服务进程可用；外部服务调用结果以查询接口为准。

默认 CORS 允许本机 `localhost:5173` 和 `127.0.0.1:5173`。前端改了端口或部署域名后，后端同学将实际来源加入 `FOOD_CORS_ORIGINS` 的 JSON 数组，并重启服务。来源写协议、主机与端口，不写 URL 路径。部署时可让现有后端反向代理 `/api/search/food`，前端使用同源地址。

## 2. 前端怎么放输入数据

请求使用 `Content-Type: application/json`。可直接发送表单字段，也可只发自然语言：

```json
{
  "query": "杭州西湖附近的杭帮菜，人均不超过100元，推荐10家"
}
```

建议同时提供明确的 `destination` 和结构化条件，减少自然语言歧义。完整可运行示例见 [examples/food_request.json](examples/food_request.json)：

```json
{
  "destination": "杭州",
  "query": "西湖附近的杭帮菜，两个人吃饭，每人人均不超过100元",
  "location": {
    "longitude": 120.1488,
    "latitude": 30.2424,
    "coordinate_system": "GCJ-02"
  },
  "radius_m": 3000,
  "cuisines": ["杭帮菜"],
  "keywords": [],
  "max_price_per_person": 100,
  "min_rating": null,
  "limit": 10,
  "include_unknown_price": false,
  "include_unknown_rating": false
}
```

上面的经纬度是检索中心示例，实际应使用酒店、景点或用户选定位置的高德坐标。

只提供 `query` 时，查询描述中必须能提取城市；只提供 `location` 不够，位置不会被自动反查为目的地。

| 前端控件/输入 | 请求字段 | 格式与约定 |
| --- | --- | --- |
| 目的地城市 | `destination` | 最多 100 字符，可省略；与 `query` 至少提供一项 |
| 用户餐饮描述 | `query` | 最多 2000 字符，可省略；由 `qwen-max` 提取条件 |
| 附近搜索中心 | `location` | 对象：`longitude`、`latitude`、`coordinate_system` |
| 搜索半径 | `radius_m` | 整数米，默认 `3000`，范围 `1..50000`；附近检索需提供中心 |
| 菜系多选 | `cuisines` | 最多 6 个字符串，例如 `["川菜", "粤菜"]`，默认 `[]` |
| 其他搜索词 | `keywords` | 最多 6 个字符串，例如 `["火锅"]`，默认 `[]` |
| 每人每餐预算上限 | `max_price_per_person` | 正数或 `null`，单位人民币/人/餐 |
| 最低评分 | `min_rating` | 数字 `0..5` 或 `null` |
| 期望候选数量 | `limit` | 整数，默认 `10`，范围 `1..50` |
| 预算筛选允许价格缺失 | `include_unknown_price` | 布尔值，默认 `false` |
| 评分筛选允许评分缺失 | `include_unknown_rating` | 布尔值，默认 `false` |

明确发送的结构化字段优先于从 `query` 提取的同名条件。为了让自然语言决定某个条件，前端应省略该字段，不要把表单默认值自动塞进去覆盖用户描述。无预算或评分限制时，省略对应字段或传 `null`。

“省略”和“显式清空”含义不同：假设 `query` 写了“人均 80 元以内”，省略 `max_price_per_person` 会采用千问提取的 80；显式传 `max_price_per_person: null` 会清除该限制。`min_rating: null` 同理；显式传 `cuisines: []` 或 `keywords: []` 会覆盖自然语言提取的对应列表。因此只有用户选择“不限”或明确清空条件时，才主动发送 `null` / `[]`。希望模型提取目的地时，省略 `destination`，不要发送 `destination: null`。

每个菜系或关键词须为 1..80 字符，不能包含 `|`。接口拒绝未定义字段；不要把整个用户画像或行程表单直接展开到餐饮请求中。

`max_price_per_person` 不能填写整次旅行的 `budget` 或 `total_budget`。例如两个人这一餐预算 200 元，对应 `max_price_per_person: 100`。用户画像、旅行日期、人数与全程预算传给 PlanAgent，由规划模块统筹。

坐标只接受高德 **GCJ-02**；`longitude` 是经度，`latitude` 是纬度，不能互换。浏览器 `navigator.geolocation` 的原始 WGS84 坐标必须先通过地图 SDK 或后端坐标转换接口转为 GCJ-02。不能仅把 `coordinate_system` 字符串改成 `GCJ-02`。没有中心时使用城市内文本检索，此时无法提供到用户中心的距离。

有预算上限时，价格超过上限的候选会被排除；价格缺失的候选默认也会被排除。`include_unknown_price: true` 允许未知价格的候选保留，但其价格仍为 `null`。评分筛选与 `include_unknown_rating` 同理。这两个开关不能把缺失值当作满足限制；前端应明确展示“价格未知”或“评分未知”。

`cuisines` 用作高德搜索词，以多个菜系的候选合并召回，不保证每家候选都有明确的菜系数据。每个菜系会与全部 `keywords` 组合搜索；未选菜系时，各个关键词分别召回。PlanAgent 根据返回的 `cuisine` 与 `cuisine_source` 再判断是否适合用户。

`limit` 是期望的候选数量，返回可以少于该数。搜索词、评分与预算筛选以及高德数据覆盖都会影响数量；没有匹配时返回 `food: []`，前端显示“暂无符合条件的餐厅”，并展示 `warnings`。一次最多检索 4 组搜索词，每组最多 3 页、每页 25 条；高德检索总时间上限为 60 秒，候选不代表全部餐厅。

## 3. 前端调用示例

可将 [examples/food-client.ts](examples/food-client.ts) 复制到前端 `src/api/food.ts`，其中包含请求/响应类型、调用函数、错误处理和传给 PlanAgent 的数据拼装示例。使用方式：

```ts
import { searchFood, exampleFoodRequest } from './api/food';

const result = await searchFood(exampleFoodRequest);
// 餐厅卡片使用 result.food，页面提示使用 result.warnings。
for (const restaurant of result.food) {
  const priceLabel = restaurant.price_per_person === null
    ? '人均未知'
    : `¥${restaurant.price_per_person}/人`;
  const ratingLabel = restaurant.rating === null
    ? '暂无评分'
    : `${restaurant.rating}/${restaurant.rating_scale}`;
  console.log(restaurant.name, priceLabel, ratingLabel, restaurant.url);
}
```

同源反向代理时调用 `searchFood(exampleFoodRequest, '')`。跨域部署时将第二个参数换成后端餐饮服务地址。前端通过 `<a href={restaurant.url} target="_blank" rel="noopener noreferrer">查看高德地图</a>` 展示地图链接。

可以先用 curl 联调：

```bash
curl -X POST http://127.0.0.1:8001/api/search/food \
  -H 'Content-Type: application/json' \
  --data-binary @SearchAgent/examples/food_request.json
```

## 4. 返回数据与展示约定

顶层响应：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `destination` | string | 最终解析出的目的地 |
| `resolved_query` | object | 合并后的条件、原始 `query`，以及 `search_keywords`、`candidate_count`、`matched_count`、`truncated` |
| `food` | Restaurant[] | 去重、筛选后的候选餐厅 |
| `warnings` | string[] | 数据缺失、搜索范围等提示，需传递给下游 |
| `source` | `"amap"` | 餐厅事实来自高德 |
| `coordinate_system` | `"GCJ-02"` | 返回位置坐标系 |
| `fetched_at` | string | ISO 8601 检索时间 |

每条 `Restaurant`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 高德 POI ID，可用于去重 |
| `name` / `title` | string | 餐厅名称；`title` 兼容已有搜索结果格式 |
| `content` | string | 由已获得字段生成的简短摘要，兼容已有格式 |
| `address` | string | 地址 |
| `location` | GeoPoint 或 `null` | `{longitude, latitude, coordinate_system: "GCJ-02"}` |
| `price_per_person` | number 或 `null` | 人均消费参考，缺失为 `null` |
| `currency` / `price_unit` | `"CNY"` / `"person/meal"` | 价格单位 |
| `rating` | number 或 `null` | 高德返回的评分，缺失为 `null` |
| `rating_scale` | `5` | 本模块统一接口约定；不是本模块计算的评分 |
| `cuisine` / `cuisine_source` | string 或 `null` | 从高德明确分类或菜系标签识别；来源为 `amap_type` / `amap_atag` / `amap_tag`，无法识别时为 `null` |
| `category` / `typecode` | string | 高德 POI 分类与分类编码 |
| `tags` | string[] | 高德提供的特色标签 |
| `business_area` | string 或 `null` | 商圈 |
| `telephone` / `website` | string 或 `null` | 高德返回的电话、网站；覆盖不保证 |
| `url` | string | 高德地图查看链接，独立于 `website` |
| `opening_hours` | `null` | 当前模块没有可用营业时间来源，保留字段供后续扩展 |
| `distance_m` | number 或 `null` | 到请求中心的直线距离，单位米 |
| `distance_kind` | `"straight_line"` 或 `null` | 不表示步行/驾车路线距离或所需时间 |
| `reviews` | `null` | 不提供用户评论正文 |
| `missing_fields` | string[] | 位置、价格、评分、菜系等重要规划字段的缺失标记 |
| `source` / `fetched_at` | `"amap"` / string | 事实来源与检索时间 |

高德的价格、评分和菜系覆盖不保证。缺失使用 `null`，不要替换为 `0`；不要让模型补出价格、评分或用户评价。`cuisine_source` 用于说明识别依据，标签和菜系不能作为过敏、素食、清真等饮食要求的保证。

高德原始价格或评分为 `0`、空数组或无效值时，也按未知处理；评分超出本模块 `0..5` 范围时按未知处理。`resolved_query.candidate_count` 为去重后的候选数，`matched_count` 为筛选后、截取 `limit` 前的数量，`truncated: true` 表示搜索组数、分页、时间或部分接口失败导致检索不完整。

原始 `query` 保存在 `resolved_query`，方便 PlanAgent 继续处理忌口与偏好。查询含过敏、忌口等条件时，`warnings` 会说明本模块未验证餐厅是否满足这些要求；不能仅因该店被召回，就认定其符合饮食限制。

高德 POI 搜索会在部分餐饮点的 `biz_ext` 返回 `cost` 和 `rating`；这些是消费和评分参考，不是当前菜单、实时优惠、订座报价或用户评论正文。详见 [高德地点搜索文档](https://developer.amap.com/api/webservice/guide/api-advanced/search)。地图链接遵循 [高德 URI API 地点详情规则](https://lbs.amap.com/api/uri-api/guide/mobile-web/information)。

## 5. 交给 PlanAgent 的数据位置

下游统一把完整餐厅数组放在 **`search.food`**：

```python
# food_result 是独立 HTTP/CLI 返回的餐饮响应。
search_result["food"] = food_result["food"]
search_result["food_meta"] = {
    key: food_result[key]
    for key in ("warnings", "resolved_query", "fetched_at", "source", "coordinate_system")
}

# profile/basic 等内容由负责规划的模块提供。
plan_input = {
    "search": search_result,
    "profile": user_profile,
    "basic": trip_basic,
}
```

PlanAgent 应保留 `id`、`name`、`address`、`location`、`price_per_person`、`currency`、`price_unit`、`rating`、`rating_scale`、`cuisine`、`cuisine_source`、`distance_m`、`missing_fields`、`url` 等字段，并读取 `food_meta.warnings`。跨餐厅与景点比较距离时，各类位置需使用同一坐标系。`distance_m` 只针对本次检索中心；候选之间的距离和出行时间由规划模块另行计算。

现有整体后端的 `POST /api/plan` 和 SearchAgent 的 `run_search` 使用以下嵌套输入传递餐饮条件：

```json
{
  "destination": "杭州",
  "start_date": "2026-10-10",
  "end_date": "2026-10-12",
  "food_options": {
    "cuisines": ["杭帮菜"],
    "max_price_per_person": 100,
    "limit": 10
  }
}
```

整体搜索仍返回 `food: Restaurant[]`，餐饮来源、查询条件和提示放入 `food_meta`。独立餐饮接口请求不套 `food_options`。

本次已接通餐饮数据传递：

1. 现有后端 `/api/plan` 接收 `food_options`，经 `Orchestrator.search_node` 转给 `run_search`，包括局部修改行程时的搜索。
2. `PlanAgent._trim_search` 保留餐厅位置、价格、评分、菜系及其来源、单位、缺失标记和 `food_meta`，供规划模型读取。链接保存在原始 `search.food`，并由现有 `_backfill_links` 按名称回填到行程。规划算法和用户画像逻辑仍由规划同学维护。

独立餐饮服务默认在 `8001`；整体 `/api/plan` 使用已有 backend 服务，项目默认在 `8000`，运行依赖沿用整体项目的配置。只安装 `requirements-food.txt` 即可独立测试餐饮；完整行程还需要配置其他智能体和搜索工具。

前端同学在现有 `frontend/src/api/client.ts` 的 `api.plan` 参数类型中增加 `food_options?: FoodSearchRequest`，导入示例中导出的 `FoodSearchRequest` 即可发送上述 JSON。本次未修改前端 UI 或其类型声明。用户画像继续放 `profile`，人数/全程预算放 `basic`，餐饮查询放 `food_options`。餐饮自然语言放 `food_options.query`，已有顶层 `query` 仍由整体旅行查询入口处理。

规划同学还需提供景点/酒店的可比较位置，并计算候选之间的路程和出行时间。前端现在可先直接联调独立餐饮接口，再按上述结构把餐饮条件随计划请求提交。

## 6. CLI 与接口错误

CLI 无需启动 HTTP 服务：

```bash
python -m SearchAgent.food_cli --file SearchAgent/examples/food_request.json
python -m SearchAgent.food_cli --query '杭州找川菜，人均80元以内'
python -m SearchAgent.food_cli --json '{"destination":"杭州","cuisines":["川菜"]}'
python -m SearchAgent.food_cli --schema
```

也可通过标准输入传入 JSON 或自然语言；`--file -` 表示从标准输入读取。成功时标准输出为餐饮响应 JSON，退出码 `0`；失败时标准输出为错误 JSON，退出码 `1`。`--schema` 返回 `request` 与 `response` 的 JSON Schema，供前后端核对字段。

HTTP 错误使用以下安全格式，不回显密钥或上游原始响应：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "请求参数无效",
    "details": [
      {"loc": ["body", "limit"], "msg": "参数超出允许范围", "type": "validation_error"}
    ]
  }
}
```

`details` 为可选字段，参数校验错误可带字段位置与原因。前端展示 `error.message`；需要高亮表单错误时读取 `error.details`。具体文案和校验类型以实际响应为准。

| HTTP 状态 | 含义 | 前端处理 |
| --- | --- | --- |
| `422` | 参数无效或自然语言条件无法解析 | 提示修改输入 |
| `503` | 后端未配置所需密钥 | 提示服务配置问题，由后端排查 |
| `502` | 高德或千问调用失败 | 展示查询失败，允许稍后重试 |
| `504` | 上游调用超时 | 提示超时，允许重试 |
| `500` | 未预期的服务异常 | 提示服务异常，由后端查看日志 |

`200` 且 `food: []` 表示未获得符合条件的候选，与服务错误分开处理。错误和 `warnings` 都需要展示或传给规划端，避免把失败默认为“用户不需要餐饮推荐”。
