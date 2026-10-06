# TravelAgent 完整流程筛查

检查日期：2026-10-06（北京时间）。版本：`e7d692e`。

**结论：当前交付状态下，完整流程没有跑通。补齐运行环境后，仍存在预算错误、记忆未读取、审核不纠正、修改后数据不一致，以及历史行程无法重新打开等问题，不能据此认定已经完成项目目标。**

本轮按用户要求优先验证“启动 → 登录 → 画像/旅行需求 → 检索和生成 → 审核 → 修改 → 保存 → 再次打开”。仓库没有正式验收标准；双方案、动态问卷等仅按现有文档核对，不视为用户已经确认的必验要求。

## 实际验证结果

| 环节 | 结果 | 证据/边界 |
| --- | --- | --- |
| 前端安装与编译 | 通过 | `npm ci`、`npm run build` 成功 |
| Python 语法 | 通过 | 58 个 Python 文件可被 AST 解析 |
| 后端启动 | 通过 | 安装后端依赖后启动；测试数据存放在 `/tmp` 独立数据库 |
| 现有后端测试 | 通过 | 2 个测试，只检查健康接口和目的地初始化 |
| 开发模式登录 | 通过 | 发码接口返回验证码，登录返回用户标识；不代表生产鉴权已经实现 |
| 画像保存/读取 | 通过 | TestClient 调用均返回 HTTP 200，字段保留 |
| 页面收集旅行需求 | 部分通过 | 浏览器实测：首句已包含出发地、目的地、日期、人数、预算，仍从“您想去哪里？”开始固定问答；首句字段没有用于填充 |
| 自然语言生成 | 失败 | `/api/plan` 返回 HTTP 200 + `{"error":"无法从输入中解析出目的地或日期"}`；实际解析解释器缺失被吞掉 |
| 结构化生成 | 失败 | `/api/plan` 返回不存在的 `Orchestrator/.venv/bin/python` 错误 |
| 搜索与意图识别 | 失败 | `/api/search`、`/api/question` 分别缺 SearchAgent、PlanAgent 的解释器 |
| 流式澄清 | 通过 | 未填写出发地时返回 `clarify` 和 `[DONE]` |
| 流式生成 | 失败 | 浏览器填完八题后发出 `/api/plan/stream` 请求；后端先发 HTTP 200，再因编排器解释器缺失抛 `FileNotFoundError`；检查时页面仍显示“正在规划” |
| 保存/读取行程数据 | 部分通过 | 用**合成行程**独立验证 `/trip-memory` 写入与读取成功；这不是实际生成成功的证据 |
| 历史行程再次打开 | 未形成闭环 | 历史页只显示摘要，没有加载完整行程或继续修改的入口；详情页读取另一个 `/trips` 数据源 |
| 真实 LLM/实时检索质量和耗时 | 初次筛查时未验证 | 当时没有项目 `.env`、Agent `.venv`、FlyAI CLI；后续提供凭据后的千问/高德测试见报告末尾。完整多工具流程仍未验证 |

测试时明确设置 `DEBUG=false`，因为宿主环境已有 `DEBUG=release`。采用 Python 3.14.8 和依赖声明允许安装的版本；尚未单独验证 README 指定的 Python 3.13。前端/后端安装正常不等于各 Agent 环境已部署。

## 应优先处理的问题

1. **P1：主流程的运行环境没有随交付接通，Docker 配置也无法运行完整项目。**
   后端固定启动各 Agent 目录的 `.venv/bin/python`，仅按主 README 安装后端依赖不会创建这些环境。实测结构化生成、搜索和意图识别因此失败。`/plan/stream` 的 `Popen` 没有异常处理，不能稳定返回 SSE 错误事件。Docker 后端只构建和挂载 `backend/`，没有同级 Agent 源码/环境；前端 Vite 代理仍指向容器自己的 `127.0.0.1:8000`。Docker 部分为静态核验，本机没有 Docker，未执行容器启动。
   页面已经实际提交生成请求，服务端日志与 TestClient 的异常一致；此断点不是仅凭代码推测。
   定位：[`backend/app/api/routes/plan.py:10`](backend/app/api/routes/plan.py#L10)、[`backend/app/api/routes/plan.py:238`](backend/app/api/routes/plan.py#L238)、[`backend/Dockerfile`](backend/Dockerfile)、[`docker-compose.yml`](docker-compose.yml)、[`frontend/vite.config.ts:11`](frontend/vite.config.ts#L11)。

2. **P1：长期记忆可以保存，却没有进入规划流程。**
   Orchestrator 的 `State` 没有 `user_id`，记忆节点却依赖此字段。真实 LangGraph 测试传入 `user_id` 后，该字段被过滤；画像、长期偏好和历史行程的读取调用全部为零。结果保存从原始请求重新取用户标识，因此保存仍成功，容易误判为“记忆功能正常”。前端显式传画像只能绕过画像缺失，不能修复长期偏好/历史记忆缺失。
   定位：[`Orchestrator/orchestrator.py:160`](Orchestrator/orchestrator.py#L160)、[`Orchestrator/orchestrator.py:303`](Orchestrator/orchestrator.py#L303)。

3. **P1：费用漏算，超预算方案可能显示为预算正常。**
   餐饮块名为“午餐（2家可选）”，实际餐厅与价格在 `options` 中；价格函数仅按块名查询，结果把餐饮记为 0。离线复现：门票 100、酒店 1000、午餐选项 500/600、预算 1500，返回 `total_cost=1100`、`budget_status=ok`，但最低实际费用为 1600。人数也没有参与此成本函数，人均消费不能正确累计。景点搜索归一化还丢弃源票价字段，未知价格被当作 0。
   前端“总消费”同样直接加总 `blocks.price`，没有处理餐厅选项或人数，会展示漏算后的价格（`frontend/src/pages/AgentPage.tsx:1775`）。
   定位：[`PlanAgent/plan.py:1792`](PlanAgent/plan.py#L1792)、[`PlanAgent/plan.py:1828`](PlanAgent/plan.py#L1828)、[`SearchAgent/tools.py:654`](SearchAgent/tools.py#L654)。

4. **P1：审核结果不能保证方案可执行。**
   模型连续两次没有返回有效的布尔 `passed` 时，ValidateAgent 默认返回通过，已用 `not json`、`{}` 复现。即使审核正确指出高严重性问题，`MAX_ITERATIONS=1` 也会结束并保存失败方案。后一项是 `OPTIMIZATION.md` 明确记录的提速取舍，不应误称随机合并回归，但它确实关闭了自动纠正能力。修改流程也跳过审核。
   定位：[`ValidateAgent/validate.py:83`](ValidateAgent/validate.py#L83)、[`Orchestrator/orchestrator.py:38`](Orchestrator/orchestrator.py#L38)、[`Orchestrator/orchestrator.py:288`](Orchestrator/orchestrator.py#L288)。

5. **P1：修改后没有完整重算价格、坐标和路线。**
   完整修改分支重建 `blocks`，却没有执行生成分支的价格补充和路线计算。离线替换景点/酒店后，新的块没有 `price/lng/lat`，旧总价、预算状态和路线仍可能保留，行程与地图/费用不一致。局部修改也缺少成本重算和再次审核。
   定位：[`PlanAgent/plan.py:1892`](PlanAgent/plan.py#L1892)、[`PlanAgent/plan.py:1959`](PlanAgent/plan.py#L1959)。

6. **P1：保存后重新打开完整行程的用户流程没有实现。**
   确认方案保存到 `/trip-memory`，历史页只显示目的地、日期、方案名称与评价，不提供点击打开、地图或继续对话。`/trips/:id` 详情页读取独立的 `/trips/{id}`；保存记忆后 `/trips` 列表仍为空，已实测。数据能落库，但不是可恢复、可继续编辑的完整流程。
   定位：[`frontend/src/pages/AgentPage.tsx:1449`](frontend/src/pages/AgentPage.tsx#L1449)、[`frontend/src/pages/TripsPage.tsx:22`](frontend/src/pages/TripsPage.tsx#L22)、[`frontend/src/pages/TripDetailPage.tsx:9`](frontend/src/pages/TripDetailPage.tsx#L9)。

7. **P1/P2：搜索和后处理允许产生空行程或时间冲突。**
   酒店、景点工具异常被吞成空列表并缓存；模拟 CLI 不存在时，真实函数只返回 `[]`。无景点时 PlanAgent 仍输出“3 日游”，但 `itinerary=[]`，没有错误标识。地理后处理还会把第二天活动移入第一天而不重新排时间；离线复现后，第一天两个景点均为 `09:00–11:00`。
   定位：[`SearchAgent/tools.py:337`](SearchAgent/tools.py#L337)、[`SearchAgent/tools.py:703`](SearchAgent/tools.py#L703)、[`PlanAgent/plan.py:661`](PlanAgent/plan.py#L661)、[`PlanAgent/plan.py:826`](PlanAgent/plan.py#L826)。

## 其他已确认问题

| 问题 | 影响 | 定位/验证 |
| --- | --- | --- |
| 第一句完整需求没有解析 | 用户必须重新填写固定八题，已写出的预算/人数等不会自动保留 | `frontend/src/pages/AgentPage.tsx:1238`；真实浏览器复现 |
| 流式接口 HTTP 200 返回 JSON error 时前端静默结束 | 页面可能保持“正在规划”文案，没有失败原因 | `frontend/src/hooks/usePlanStream.ts:55`；对原 hook 转译执行，模拟响应后 `events=[]` |
| 候选项目的添加/移除和保存方案不同步 | 详情弹窗只更新 `planItemIds`，没有修改 `routePlan.blocks`；移除的项目可继续出现在保存结果中 | `frontend/src/pages/AgentPage.tsx:1249/1259/2495`；静态核验 |
| 首次规划信息没有保留供修改使用 | 修改从 `tripInfo` 读取，但固定八题流程没有写这个键，可能失去预算、人数或使用上一次旅行的信息 | `frontend/src/pages/AgentPage.tsx:848/642`；静态核验 |
| 同浏览器切换账号串用画像 | 退出只清 `currentUser`，全局 `userProfile` 仍属于前一个账号，且未从服务端加载新账号画像 | `frontend/src/pages/ProfilePage.tsx:36`、`frontend/src/components/Layout.tsx:27`；静态核验 |
| 手动加入付费项目不增加费用 | 新建 block 没有 price，费用显示仍按 0 累加 | `frontend/src/pages/AgentPage.tsx:1356`；静态核验 |
| 选项问题的聊天文本回答丢失 | 直接输入“2人”等文字会推进下一题，但保存的是空的按钮选中数组 | `frontend/src/pages/AgentPage.tsx:879`；静态核验 |
| 搜索缓存不包含画像 | 相同城市和日期的文化游用户可能拿到上一次亲子游候选 | `SearchAgent/tools.py:1313`；离线复现第二次没有重新检索 |
| 餐饮预算固定按两天六餐计算 | 三天、五天等行程的人均餐费上限不随天数变化 | `SearchAgent/tools.py:1380` |
| MCP 酒店/火车/餐饮 wrapper 位置参数错位 | 排序被当成关键词/行程类型，餐厅数量被当作菜系关键词 | `SearchAgent/tools.py:1177/1207/1249`；三项离线复现；JSON 主链未经过这些 wrapper |
| 独立审核循环仍使用旧参数顺序 | search/basic/answers 绑定到错误参数，预算上下文丢失 | `ValidateAgent/run.py:42`；离线核验 |
| `/trips` 同日期再次保存不更新 items | 标题变成新值，实际景点仍是旧值 | `backend/app/api/routes/trips.py:47`；TestClient 实测 |
| 同日期记忆 upsert 覆盖全部默认字段 | 再次自动保存可清空原有评分、反馈和所选方案 | `backend/app/api/routes/trip_memory.py:29`；TestClient 实测 |
| `dislike` 被识别为正向行为 | 不喜欢的对象被学习为喜欢 | `backend/app/services/memory_service.py:56`；`_action_polarity('dislike') == 1` 实测 |
| 无服务端身份与资源归属校验 | 不登录即可读取指定用户画像/记忆，删除任意 ID 的行程 | 相关路由仅接受 user_id；测试仅操作隔离数据库中的合成用户/行程 |
| 日期未校验先后关系 | 结束日期早于开始日期仍可创建行程 | `backend/app/schemas/trip.py`；TestClient 实测 HTTP 200 |
| 文档与实现不一致 | 当前只输出一个“推荐方案”，图中没有 QuestionnaireAgent 节点 | `PlanAgent/plan.py:1478`、`Orchestrator/orchestrator.py:353`；旧文档写双方案/动态问卷 |

远期天气直接调用预报 API，也需要明确能力范围与降级提示；[Open-Meteo 官方文档](https://open-meteo.com/en/docs)说明预报最多支持 16 天。此项没有调用实际天气接口。准确景点票价、开放时间、酒店可订状态等外部数据仍需在配置齐全后验收。

## 建议验收顺序

1. 统一可复现的启动方式和依赖/配置，验证页面提交的需求确实到达全部 Agent；外部服务失败应明确报错。
2. 修复记忆传递，定义预算中人数、房间数、晚数、可选餐厅和未知价格的规则，并对审核失败实施纠正或明确拒绝输出。
3. 让修改后重新计算时间冲突、预算与路线，并保存可恢复的完整行程。
4. 补历史行程打开与继续编辑，然后用真实检索数据验收“正常生成、低预算、老人/儿童、远期日期、服务失败、修改后恢复”等场景。
5. 进入多人使用前补齐鉴权和资源归属校验。

本轮没有修改业务代码；前端依赖与构建产物位于 git 忽略目录。后端测试使用 `/tmp` 数据库，没有修改已有用户数据。离线编排测试使用真实 LangGraph，外部调用被替换为明确的模拟输出，验证的是状态传递和控制流程，不是模型质量。

本轮启动的 Vite 和后端服务已关闭。浏览器截图返回的画面与即时可访问性树不一致，未作为证据使用；页面观察已通过实际请求和后端日志交叉核验。

## 餐饮搜索专项复查（2026-10-06）

高德 v5 的 `business.rating/cost/business_area` 字段读取、城市范围限制、结构化地址/坐标/链接，以及价格缺失用 `None` 表示的方向正确。[高德官方 POI 2.0 文档](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)与这些核心请求参数和字段位置一致。仍未配置 AMAP_KEY，因此不声称已经通过真实高德检索验收。

本轮执行餐饮现有两个本地单元测试，均通过；另用模拟响应执行实际高德函数，并用 AST 提取实际检索/规划函数做离线复现。

| 范围 | 问题 | 复现/影响 | 建议 |
| --- | --- | --- | --- |
| 餐饮 MCP 入口 | `tools.py:1249` 把 max_results 传入 keyword | `search_food('杭州',5)` 实际 keyword=5、数量仍默认50；JSON 主链使用命名参数，未受该错误影响 | 改为 `_fetch_food(destination, max_results=max_results)`；按需求向 MCP 暴露 keyword/max_price |
| 餐饮预算估算 | `tools.py:1380` 写死两天六餐 | 两人预算6000，2天和7天都得到125元/人/餐 | 按真实天数、人数和用餐次数计算；明确25%预算占比是估算策略 |
| 餐饮降级与规划对接 | `tools.py:953` 的 Tavily 输出只有 title/content/url | `_pick_restaurants` 要求 name；实际模拟降级结果送入 `_add_food_to_day` 后没有添加任何用餐安排 | 明确降级状态；网页参考与可安排的结构化餐厅采用不同契约，提取后再校验餐厅信息 |
| 高德响应解析 | `amap_service.py:205` 直接 float() | 模拟 rating 或 cost='--'，整次餐厅搜索抛 ValueError；可能令已有有效餐厅也丢失并触发降级 | 数值解析容错；异常字段置 None，单条异常不影响整批 |
| 餐饮测试 | `test_dining_agent.py:154` mock 目标错误 | tools 已导入 search_restaurants，patch amap_service 不会替换 tools 内绑定；只断言list也不能证明执行降级 | patch `tools.search_restaurants`；断言 Tavily 被调用、来源标记和返回字段 |
| 需求链路对接 | `Orchestrator/orchestrator.py:208` 未传 food_keyword | 检索函数支持关键词，但主编排 payload 没有该字段；用户指定菜系没有完整透传链路 | 从需求收集/解析到编排统一传 keyword/饮食约束；这是跨模块修改 |

另有两个需要确定策略的边界：预算筛选保留价格未知的餐厅，不能把它们认定为已验证预算达标；同名但不同 poi_id 的门店当前只保留第一家，可能减少可用分店。两项均已用模拟数据复现。高德已能提供营业时间，但当前数据契约没有保留，后续若要保证用餐时段可执行，应补字段并由规划器检查。

此前“总消费漏算餐饮/人数”位于 PlanAgent 的费用汇总与前端显示；餐饮搜索已经提供 `price_per_person`，该问题需要与规划模块共同修复。

## 凭据接入后的真实 API 复测（2026-10-06）

用户要求使用提供的千问与高德 Key。已写入 `SearchAgent/.env`、`PlanAgent/.env`、`ValidateAgent/.env`，这些文件由 `.gitignore` 排除。具体密钥不写入报告、复现输出或 Git。配置采用千问官方 model ID `qwen3.8-max` ([模型文档](https://help.aliyun.com/en/model-studio/qwen3-8-max)) 与 OpenAI 兼容接口 ([官方调用说明](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope))。

真实验证结果：

- Qwen Chat Completions JSON 模式成功，返回的模型标识是 `qwen3.8-max`。
- 项目 `_extract_item_features` 成功通过 Qwen 将杭州餐饮内容提炼为“杭帮菜、东坡肉、龙井虾仁、人均120”等标签。
- 高德搜索“杭州/杭帮菜”返回 3 家结构化餐厅，样本含地址、坐标、人均、评分、菜系和地图链接。
- 项目 `_fetch_food` 接上高德成功；设置人均上限 200 元后返回 3 家，人均价格为 166、102、60，保留 `_source=amap` 和规划所需字段。
- `TestRestaurantInfoLocal` 两个不依赖网络的测试通过。

这证明当前提供的 Key、千问区域与选定地址至少能完成本轮请求；尚未验证完整 SearchAgent 多工具流程，也没有 Tavily Key，因此不能据此确认网页降级路径。真实测试脚本仅在 `/tmp/travel-agent-live-dining-test.py`，输出摘要在 `/tmp`，均不含密钥。

临时复现产物：`/tmp/travel-agent-backend-audit.py`、`/tmp/travel-agent-backend-audit-results.json`、`/tmp/travel-agent-agent-audit.py`、`/tmp/travel-agent-graph-audit.py`、`/tmp/travel-agent-graph-audit-results.txt`、`/tmp/travel-agent-stream-audit.cjs`、`/tmp/travel-agent-dining-audit.py`、`/tmp/travel-agent-dining-audit-results.json`。
