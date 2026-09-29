# P0.2 / P0.3 实施与地图探测报告

2026-09-29。用户已明确调整范围：先完成国内，国外后续再做。结论：P0 国内验收完成；P0.2 与上海 P0.3 已完成，海外真实验证移至后续，保留新加坡为未来试验城市。

## 国内验收边界

需求/地点/行动/计划/会话契约、上海高德真实接口、错误处理、限额与测试报告均已交付。当前采用供应商内容仅内存、不缓存、不长期存储的保守策略；前端类型以 P1 正式 OpenAPI 为基准，地图配套高德渲染。
完整五日规划、地点通用归并、用户锁定与模型调用闭环仍在 P1/P2，前端地图与卡片在 P3。国内 P0 完成不表示这些后续功能已实现。任何供应商内容持久化需在对应功能实施前确认权限。

## 已实现

- schemas：需求、地点、路线/计划、八类 Agent 行动、会话/问题/答案，以及单位/来源公共结构。旧生成接口尚未消费新结构。
- 高德 v5：关键字查点、ID 详情、周边查点、步行/驾车/公交单段路线。公交最小配置限定上海；其他城市的 citycode 与能力选择在后续阶段扩展。
- Google：Places New 关键字/详情和 Routes 三模式单段路线的最小代码适配；离线合成响应测试通过，账号与实际新加坡覆盖未验证。不是海外已上线。
- HTTP：默认最多 12 次请求、15 秒超时、无自动重试；计数包含失败请求；区分权限、额度、无路线、错误响应。SecretStr 配置，日志不输出 URL、密钥和原始响应。
- 策略：原始响应和线路几何仅内存使用；报告只保留脱敏验证元信息、状态与字段是否存在，未永久保存路线内容。Google 地图展示配套要求保留在 policy 中。

## 上海真实结果

最终探测时间：2026-09-29 15:33（Asia/Shanghai）；公交请求时刻：2026-09-30 10:00 +08:00。
最终单轮 11 次实际请求，均未触发额度/权限错误。样例精确名称加景点类别匹配用于试验，不能当作通用地点归并算法。

| 验证 | 结果 |
|---|---|
| 豫园、上海城隍庙、外滩查点 | 均有供应商 ID 与 GCJ02 坐标；结果保持 candidate，不自动成为用户已确认选择 |
| 豫园 ID 详情 | 供应商身份与搜索一致，坐标存在 |
| 豫园附近餐馆 | 返回候选；不代表个性化排名或库存可用 |
| 豫园 → 上海城隍庙，步行/驾车 | 均返回距离、耗时、折线 |
| 豫园 → 上海城隍庙，公交 | API 成功，但无公交路线；no_route，耗时/距离为 null |
| 上海城隍庙 → 外滩，步行/公交/驾车 | 均返回距离、耗时、折线；公交传入日期和出发时刻 |

“探测通过”标准：响应转换正确，包括明确的 no_route；每个模式至少一条可用路线；可用路线有几何。不表示所有地点之间都存在公交，也不表示实际旅途必定按估计耗时完成。
步行/驾车 v5 最小接口未应用未来出发时刻，RouteLeg.departure_time_applied=false，并附警告；不能将这些估计当作指定未来时刻的路况预测。

现场发现并修复：豫园返回“上海豫园”，城隍庙和外滩有同名非景点候选；v5 公交 polyline 存在额外嵌套对象。前者由明确试验样例与类别筛选解决，通用地点解析留给 P2；后者已在适配和离线测试中覆盖。

证据文件：
- probe-shanghai.json：最终通过记录。
- probe-shanghai-initial.json：首次身份歧义记录，3 次请求。
- probe-shanghai-pre-geometry.json：公交折线解析修正前记录，11 次请求。
- 另有候选核对与公交字段结构诊断共 9 次请求；本次上海累计请求数 34，最终单轮仍受 12 次上限约束。没有扩大矩阵或后台重复探测。

## 新加坡状态

此项已按用户要求暂缓，不属于当前国内 P0 的完成条件，也不阻塞 P1–P3。以下是暂缓前的真实记录，未改写成测试成功。
probe-singapore.json：blocked / missing_credentials；0 次外部请求。
高德国内最小适配明确拒绝 SG，不把这个本地限制描述成高德海外服务不可用。用户账号海外权限及相应接口尚未确认。
继续真实验证需要本地 backend/.env 的 GOOGLE_MAPS_API_KEY，项目开通 Places API (New)、Routes API 与适用计费/配额。未创建账号、开通付费产品或购买套餐。

## 测试与复现

49 项后端测试通过（23 项 schema、21 项供应商错误/转换、3 项探测状态、2 项原有健康接口）。健康接口测试使用临时 SQLite 数据库，未改用户旅行数据。compileall 与 diff 格式检查通过。

在仓库根目录执行：

```powershell
# 请求会使用 backend/.env 的本地凭据；每次显式执行才发起请求。
.\.venv\Scripts\python.exe backend/scripts/probe_maps.py --provider amap --city shanghai --report docs/p0/probe-shanghai.json
.\.venv\Scripts\python.exe backend/scripts/probe_maps.py --provider google --city singapore --report docs/p0/probe-singapore.json

# 新增离线测试不依赖真实密钥、不调用网络。
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m pytest backend/tests/test_p0_contracts.py backend/tests/test_map_contracts.py backend/tests/test_map_probe.py -q
```

探测退出码：0 通过；2 权限/配置等阻塞；3 部分完成。--max-calls 可降低调用上限；--departure-at 接受带时区偏移的 ISO 时刻。报告文件会更新为本次结果；若需保留历史，请提供不同文件名。
P0.2 本地提交：3ba1c5b；P0.3：fd688a2；远端未推送（原仓库写权限未解决）。国内 P0 已验收；海外真实覆盖与新增内容存储权限在对应后续阶段核查。本次验收只更新文档，不重复发起已通过的实测。

## 官方接口依据

- [高德 POI 2.0](https://lbs.amap.com/api/webservice/guide/api/newpoisearch)
- [高德路径规划 2.0](https://lbs.amap.com/api/webservice/guide/api/newroute)
- [高德错误码](https://lbs.amap.com/api/webservice/guide/tools/info)
- [高德开放平台条款](https://lbs.amap.com/pages/terms/)
- [Google Places Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Google Routes computeRoutes](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes)
- [Google Routes 展示与缓存要求](https://developers.google.com/maps/documentation/routes/policies)
