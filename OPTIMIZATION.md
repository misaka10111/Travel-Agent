# 生成加速优化记录

本文记录 TravelAgent 多 Agent 生成流水线的加速优化历程、当前配置与待实施方案。

## 当前状态

完整流程（自然语言「我想去杭州玩五天」→ 生成 2 个方案）实测：

- **总耗时约 91 秒**（含自然语言解析）
- 从最初「近 10 分钟（还超时）」降到 91 秒，约 8 倍加速

### 耗时分布（估算）

| 环节 | 耗时 | 说明 |
| --- | --- | --- |
| parse 自然语言解析 | ~11s | DeepSeek flash 解析目的地/日期 |
| search 数据检索 | ~22s | 6 工具并行 + 3 次特征提取 |
| plan 生成方案 | ~40s | 并行双方案（经典人气 + 小众深度） |
| validate 审核 | ~15s | 1 轮（MAX_ITERATIONS=1） |

## 已实施的优化

### 1. 全部换成 deepseek-flash

4 个 Agent（SearchAgent / PlanAgent / ValidateAgent / QuestionnaireAgent）的模型从
`deepseek-v4-pro` 换成 `deepseek-flash`。

### 2. 去掉 `reasoning_effort="low"`

实测发现该参数被 DeepSeek 错误映射，`reasoning` 反而从 5338 涨到 8949，是 JSON 截断的元凶。
**默认（不传该参数）reasoning 最少**。

### 3. PlanAgent 并行双方案

把「一次生成 2 个方案」拆成 `ThreadPoolExecutor` 2 线程并行各生成 1 个方案，
总耗时 = 取最大值而非累加。

### 4. 精简 PlanAgent 输出

- 每天固定 2 个景点 + 午餐 + 晚餐（最多 4 项）
- note 控制在 15 字以内
- 去掉 meals / tips / recommended_* 等冗余字段
- 外层元数据（destination / start_date / end_date / days / weather_summary）本地从 search 结果计算，不再让 LLM 生成

### 5. search 结果特征化精简

把传给 PlanAgent 的 search 结果从 25063 字符精简到约 11743 字符：

- 景点 description → LLM 提取特征标签（如「历史,文化,石窟,户外,亲子」）
- 活动 content → LLM 提取特征（如「音乐节,室内,9月,乐迷」）
- 美食 content → LLM 提取特征（如「杭帮菜,必吃榜,本地推荐」）

### 6. MAX_ITERATIONS = 1

validate 审核后不再退回 plan 重生成，流程固定一轮，省掉一轮 plan + validate（约 1 分钟）。

## 关键配置参数

### 各 Agent 模型（均为 deepseek-flash）

| Agent | max_tokens | 备注 |
| --- | --- | --- |
| SearchAgent parse | — | 只解析日期/地点 |
| SearchAgent 特征提取 | 6000 | poi / events / food 各一次 |
| PlanAgent 单方案 | 16000 | reasoning 占约 70%，需留足 content 空间 |
| ValidateAgent | 12000 | 重试最多 1 次 |
| QuestionnaireAgent | 8000 | 当前默认跳过 |

### DeepSeek 推理模型的坑

1. `reasoning_effort="low"` 反而增加 reasoning（不要用）。
2. reasoning 与 content 共享 `max_tokens` 预算，reasoning 通常占 70% 左右。
3. `max_tokens` 太小会被 reasoning 吃满导致 content 空 / JSON 截断。
4. 并发调用时 reasoning 波动更大，需要留足 `max_tokens` 缓冲。

## 待实施的加速方案（按收益排序）

### 1. 合并 3 次特征提取为 1 次 LLM（简单，省 ~10s）

poi、events、food 各调一次 LLM 提取特征，共 3 次并发。改成搜完所有数据后
**一次 LLM 调用批量提取全部特征**，减少 reasoning 波动。search 预计 22s → 12s。

### 2. parse 规则优先 + LLM 兜底（简单，省 ~8s）

「我想去杭州玩五天」用正则/词典提取地点+天数，仅模糊表达（"十月中旬到末"）走 LLM。
parse 预计 11s → 2s。

### 3. plan 分天并行（中等复杂，省 ~20s，收益最大但有风险）

「一次生成 6 天完整计划」改成「6 个并行调用各生成 1 天」，输出短、并行后 ≈ 单天时间。
但 2 方案 × 6 天 = 12 个并发请求可能触发 DeepSeek 限流，需重构 PlanAgent。

### 4. 流式输出 SSE（不减少真实耗时，但体验更快）

后端 subprocess 阻塞改成边生成边推给前端，用户实时看到进度。

### 5. 跳过 validate（省 ~15s，看取舍）

validate 当前只列问题、不退回修改。plan 质量稳定后可暂时跳过。

## 已知功能缺口（待办）

### 1. 交通信息未接入

- **机票**：`_fetch_flights`（飞猪 `search-flight`）工具已存在，但未接入 `run_search`，且缺 `origin` 输入。
- **市内交通**：完全没有工具（需接高德/百度地图路线 API，查询两点间地铁/打车/步行耗时）。
- **PlanAgent 交通 note 被牺牲**：精简 prompt 时删除了「注明交通方式和耗时」的规则，且 note 限制 15 字，
  导致当前生成的 schedule 完全没有交通信息。

交通方案（待定）：

- 机票：SearchAgent 输入加 `origin`，`run_search` 加 `flights` 任务，PlanAgent 直接引用（改动小）。
- 市内交通：PlanAgent 生成计划后，提取相邻活动对，批量调地图 API 填回 note（或 Orchestrator 加 transit 节点）。
- 不建议让 PlanAgent 反复调用 SearchAgent（会退化为 ReAct 循环，慢且破坏解耦架构）。

### 2. 本次旅行信息未接入生成流程

- TripSurveyPage 保存的 `tripInfo`（出发地/人数/预算/目的）只写入 localStorage，AgentPage 生成计划时未读取、未传递。
- 后端 `PlanRequest` 只有 `query/destination/start_date/end_date/profile/answers`，无 `budget/travelers/origin` 等字段。
- 之前给 Trip 模型新增的 `origin/travelers/budget_tiers/purposes` 仅用于落库，未接进 plan 流水线。

待补链路：前端 AgentPage 读 `tripInfo` 一起传给 `/api/plan` → 后端 `PlanRequest` 加字段 → Orchestrator 透传 →
PlanAgent 作为预算/人数/目的约束（`basic`）。

### 3. 性能测试基线说明

- 之前的性能测试均为「裸跑」：只传 `destination/start_date/end_date` 或 `query`，无 `profile`、无旅行信息。
- 因此测得的 91 秒是在无用户画像、无预算/人数/目的数据下的结果；接入画像和旅行信息后 prompt 会变长，耗时可能略增。
