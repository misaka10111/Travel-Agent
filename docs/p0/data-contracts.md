# P0.1 数据流盘点与契约决策草案

日期：2026-09-29。范围：主规划入口、画像、问卷、搜索归一化、计划输出。P0.1 静态盘点完成；下述新结构现已在 P0.2 实现，尚未集成旧入口。国内 P0 已验收；新加坡验证按用户要求移至海外阶段；最新结果见 map-probe-report.md。

## 1. 当前数据链与证据

| 代码位置 | 当前行为 | 需要修正的契约问题 |
|---|---|---|
| frontend/src/api/types.ts 的 UserProfile/TripInfo | 画像 5 个业务字段；问卷 11 个字段 | 缺少每字段来源、确认状态与本次快照 |
| frontend/src/pages/TripSurveyPage.tsx 的 toTripPayload | 保存目的地、出发地、日期、人数、预算和目的；companions/special_needs 在类型和初始状态中存在，但当前表单无对应编辑控件，也未写入 Trip | 不能把预留字段描述为用户已实际填写；后续补齐输入和持久化 |
| frontend/src/pages/AgentPage.tsx 的 runPlan/generate | 从 localStorage 取画像和问卷；缺字段固定追问；把答案拼成补充 query | 缺问题 ID、会话 ID、结构化回答、画像版本；字符串包含判断目的地可能误丢原问卷 |
| backend/app/api/routes/plan.py 的 PlanRequest/create_plan | query/destination/start_date/end_date/profile/basic/answers；query 且无 destination 才解析；解析出的部分字段补入 basic | 同一字段可能在顶层和 basic 重复冲突；原 query 未继续交给 Orchestrator；没有 trip_id/session_id 关联 |
| SearchAgent/search.py 的 parse_nl | 提取地点、日期、出发地、人数、预算、目的；无效或过去日期默认今天起三天 | 推测日期被当作事实；未保留未抽取的自然语言要求；天数包含首尾的口径需统一 |
| Orchestrator/orchestrator.py 的 search_node | 只传 destination/start_date/end_date 和可选 origin | 画像、预算、同行人、特殊要求未进入候选搜索 |
| SearchAgent/tools.py 的 run_search | 按固定任务搜索天气、酒店、POI、促销、活动、美食；有 origin 加往返航班/列车 | 搜索工具支持的部分价格等参数未被此入口使用；没有统一的 top 50 设置 |
| SearchAgent/tools.py 的 _fetch_hotels/_fetch_poi | 将供应商结果缩减为展示字段 | 出口没有统一地点身份、坐标、坐标系和匹配状态；不能从名称可靠恢复路线起终点 |
| PlanAgent/plan.py 的 _trim_search/build_plan | 再次摘要；模型产出名称、时间文本、note 和酒店文本；地点链接按名称匹配 | 摘要需保留内部 ID；交通建议不是已查询路线；酒店文本不是有效报价 |
| frontend/src/pages/AgentPage.tsx 的 generate | 消费 plan/plans/blocks；未将 passed/history 作为交付条件 | 有计划内容不等于计划验证通过；缺失状态不能显示为完成 |
| backend/app/schemas/trip.py、models/trip.py | 旧 Trip 与文本 ItineraryItem CRUD | 新结构增量兼容；不把旧文本地点自动转换成确认地点 |

上述为静态代码事实，不代表已运行端到端验证。供应商上游实际返回哪些字段，需要 P0.3 实测；本次仅确认当前归一化出口的不足。

## 2. 全部现有画像与问卷字段去向

| 现有字段 | 新契约目标 | 解释/转换规则 |
|---|---|---|
| profile.age_group | profile_snapshot.age_group | 年龄段不是体力约束；不自动推断步行上限 |
| profile.gender | profile_snapshot.gender | 保留兼容；不据此自动推断旅行兴趣或硬筛选 |
| profile.identity | profile_snapshot.identity | 仅背景信息；优惠资格需要独立确认 |
| profile.city | profile_snapshot.home_city | 常住城市不自动等于本次出发地 |
| profile.travel_style | preferences.interests/pace 候选 | 保存原标签；映射为可解释软偏好，有歧义保留待确认 |
| basic.destination | destination.label/region_ref | 名称保留，解析国家/城市身份；歧义不直接选首条 |
| basic.origin | origin.label/region_ref | 允许缺失；缺失时不查询假定出发地的城际交通 |
| basic.start_date | dates.start_date | ISO 日期，缺失允许草案；不能默认今天后视为已确认 |
| basic.end_date | dates.end_date | 不早于开始；游玩天数按含首尾计算；住宿晚数单独算 |
| basic.travelers | party.count/count_min/count_status/raw | “6 人及以上”存下界 6，精确人数未知；不能当作恰好 6 人报价 |
| basic.companions | party.companion_tags | 当前是预留字段；有实际答案后录入，不虚构孩子年龄 |
| basic.budget_tiers | preferences.comfort_tags | 软偏好；多选冲突可提问；“不设限”不等于无限自动消费授权 |
| basic.total_budget / Trip.budget | budget.amount/currency/scope/limit_kind | 总额与人均分开；保留原值；旧“元”显示需核对币种，海外不自动换成 SGD |
| basic.purposes | preferences.interests | 与画像合并去重，保留本次来源与优先级 |
| basic.special_needs | constraints / preferences | 当前预留字段；逐条分类；无障碍、过敏等不能仅保留为弱标签 |

其他入口字段：query → 原始 message 引用与提取结果，保留未归类要求；answers → question_id 关联的结构化答案；Trip.notes → 本次备注并保留原文；Trip.title/status → 展示/旧记录状态，不充当新任务状态；user_id → 资源归属，不信任浏览器传入值作为认证；created_at/updated_at → 审计元信息；trip_id → 旧记录关联。

旧 ItineraryItem：id/trip_id 保存 legacy 引用；day 保留原顺序；title/description/location 保存历史展示文本；start_time/end_time 在结合实际日期与时区之前不升级为确认的时刻。缺地点身份时标记 unresolved，不可直接用于算路。

既有 trip-memory 和 behavior-signal 是单独接口，本次不自动加入画像优先级；将来用于推荐时应作为弱推断并保留来源，不能覆盖显式选择。P0 不迁移这些功能。

## 3. 共同规则

1. 每个影响规划的字段附 source（profile/form/message/answer/selection/provider/inferred）、source_ref、confirmation（explicit/inferred/unknown）、updated_at。置信分不是用户确认的替代品。
2. 本次明确要求优先于长期偏好；同一旅行的硬条件冲突须提问，不能简单用最近值静默覆盖。删除和排除是显式事件，不能误当缺字段。
3. 距离米、耗时秒；金额使用十进制定点表示与币种，禁止不说明总额/人均/每晚。缺失为 null 加原因，非 0 或空字符串。
4. 经纬度显式命名并标记原坐标系；需要转换时保留转换来源。时刻含 UTC offset，地区有 IANA timezone；开放时间使用本地规则并记录日期例外。
5. schema_version、state_version、plan_version 各司其职。用户修改使状态版本递增；工具结果附 base_state_version；保存计划是独立不可变版本，不复用一个字段。

## 4. 五份核心 schema 的落地说明

### intent.py：TripIntent 与 FieldEvidence

- intent_id、schema_version、profile_snapshot_ref；origin/destination；dates（可空日期、duration_days、timezone）；party；budget；preferences；constraints；unresolved_questions；field_evidence。
- 草案允许未知日期/人数；工具声明自身前置条件，不能强迫用户在发现阶段填满所有表格。
- Constraint 有稳定 ID、字段/类型、目标值、hard/soft、来源和确认；模型不能把明确的 hard 条件降级。
- 第一版不自造通用表达式语言；明确支持必去、排除、步行限额、预约时间、已订酒店等枚举。

### place.py：Place、ProviderRef、Coordinates、Fact

- place_id 为内部 ID；name、category、region_ref；provider_refs[] 包含 provider/provider_place_id；coordinates 可空但含 longitude/latitude/crs。
- identity_status 为 unresolved/candidate/confirmed；aliases 与 parent/related 关系保留证据。豫园与城隍庙片区不能仅凭邻近就合并为同一实体。
- Fact 保存 value、source_ref、observed_at、valid_for/unknown_reason；营业时间、评分、价格不能从名称推断。
- 评分保留原尺度与来源；自有 ranking_score 单独计算，避免混淆供应商评分和个性化适合度。
- 数据契约可表达的字段不代表都允许长期存储；实际持久化受供应商策略约束。

### plan.py：Visit、RouteLeg、Stay、PlanVersion

- Visit：visit_id/place_id、日期/可选开始结束时刻、停留秒数、锁定状态、来源。非地点休息项用独立 activity 类型，不能伪造 POI。
- RouteLeg：from/to 地点引用、mode、requested_departure_at、查询时间、duration_seconds/distance_meters、provider、status、geometry_ref/CRS、warnings。公交可附步行/乘车/换乘子段。
- status 区分 ok/no_route/unsupported/permission_denied/rate_limited/unavailable/unknown；供应商响应估计与实际出行结果区分，不能称为真实发生的耗时。
- Stay：hotel_place_id、入住/退房、人数/房间条件、quote_ref；地图酒店地点不等于可预订房型。离开当天无需凭空生成住宿。
- PlanVersion：plan_id/version、base_state_version、intent_snapshot_ref、days、stays、费用覆盖范围、validation_report、status。partial 草案与 verified 可交付版本分开。

### action.py：AgentAction 与 ToolResult

动作使用 discriminated union，不接受任意工具名与任意 dict；公共字段 action_id、kind、base_state_version、scope、purpose。

| kind | 最小参数 | 工具前置条件 |
|---|---|---|
| ask_user | gap_ids、问题目标 | 未解决缺口存在；问题 ID 在执行时生成并保存 |
| search_candidates | intent_ref、类别、region_ref、filters、limit | 地区可解析；有日期限制的查询需要日期 |
| get_place_details | place_id/provider_ref、fields | 目标引用合法 |
| compute_itinerary | intent_ref、candidate_ids、selection_ref、scope | 待排候选存在；算路节点需确认坐标/身份 |
| rank_nearby | anchor_refs、category、time_window、preference_ref | 锚点有效；酒店可多个日程锚点 |
| edit_plan | plan_ref、typed_changes | 基础版本最新；不可静默解锁 |
| validate_plan | plan_ref、checks | 计划存在，缺数据输出 unknown |
| finish | plan_ref | 强制完成检查；模型不能自行设置 passed |

ToolResult：action_id、status（success/partial/error）、base_state_version、typed payload、evidence_refs、warnings、error（code/retryable/脱敏 message）、调用成本摘要。API 整体调用失败和某一条路线无结果是不同层级。

### session.py：SessionState、Question、Answer、Selection

- SessionState：session_id、owner_ref、trip_ref、state_version、intent_snapshot、候选引用、选择、当前计划引用、pending_questions、budget_usage、status。
- Question/Answer 绑定 question_id 与 state_version；多选用稳定 option_id；自由文本保留。重复提交用 request_id 幂等处理。
- Selection：selection_id、place_id、include/exclude/lock、日期/时段作用域和来源；同一地点同作用域的矛盾选择需要解决。
- proposed 状态：draft/running/waiting_user/needs_attention/completed/cancelled/failed；P1 才实现转换与数据库保存。

## 5. 合成场景与边界（设计样例，非真实地图数据）

| 场景 | 应有结果 |
|---|---|
| 上海五天但没有具体日期 | duration_days=5，日期 unknown；可找一般候选，时段路线与营业日期待定 |
| 用户画像喜欢购物，本次明确只想看园林 | 园林本次偏好优先；不因画像强塞购物 |
| “6 人及以上”、预算“5000 元” | 人数为下界；预算币种/口径按来源确认，报价前补齐必要条件 |
| 豫园、豫园景区、城隍庙片区 | 前两者只有证据一致才归并；片区关系单独表示 |
| 同名餐馆不同分店 | 保持不同 place_id，按地址/供应商身份选择 |
| 用户已锁定酒店但新路线不方便 | 保留酒店，提出替换候选或向用户说明，不静默更换 |
| 公交接口返回无路线 | RouteLeg.no_route，耗时 null；可询问其他方式，不能计成 0 |
| 用户移动景点后旧计算返回 | base_state_version 过期，不覆盖新选择 |
| 新加坡只有高德国内 Key | 能力未验证；海外实测阻塞，不生成假路线 |

## 6. 兼容与实施顺序

1. P0.2 仅新增上述 schema 与合成样例；现有 /api/plan 和旧 Trip CRUD 保持原协议。新增文件不会自动修复现有行为。
2. P0.3 供应商样例使用新 Place/RouteLeg 契约；实际响应缺失字段记录缺失，不补造。
3. P1 新接口使用上述契约和增量数据库表；旧入口通过显式适配接入，不能通过重命名文件替换。
4. P2 搜索摘要保留内部 ID；把实际路线和硬约束验证接入生成过程；修复日期默认与原文遗漏。
5. P3 前端在正式 OpenAPI 上生成/校验类型并联动版本；旧计划呈现为历史文本或待匹配草案。

## 7. P0.1 完成结论与后续验收

已覆盖 UserProfile 的 5 个业务字段、TripInfo 的 11 个字段，以及主规划入口、旧 Trip/ItineraryItem 的兼容去向。P0.2 已新增可运行 schema 与正反样例；未实施数据库迁移。P0.3 已使用它们转换实际高德响应。
23 项模型边界测试通过；全部后端测试共 49 项通过。参数有效不等于完成验证：地点归并、锁定项保护、版本竞争和 finish 的强制检查仍由 P1/P2 执行代码保障，模型本身不替代这些服务。
上海高德账号三种交通模式已有实测证据。国内 P0 采纳供应商内容仅内存的策略；未来实施供应商内容持久化前确认权限。海外供应商权限与真实测试留至海外阶段，不阻塞国内 P1。
