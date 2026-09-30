# `route-map` 分支整合记录

来源：`origin/route-map` 的 `4ee7bd8`（2026-09-30 核对）；目标：`YKF` 的 `/agent` 工作台。

## 结论

`route-map` 的 `PlanAgent/route_map.py` 确实为旧版 `blocks` 补 POI 坐标和相邻地点路线，`TripMap.tsx` 则按天画路线。当前 `/agent` 使用 `DraftDay.stops/routes`、稳定的 `place_id` 和 `SessionView`，不会读取旧版 `blocks/legs`，直接复制旧文件无法进入当前规划闭环。

| 来源 | 当前对应位置 | 决定 |
| --- | --- | --- |
| `PlanAgent/route_map.py` 地点名称检索、路线查询 | `backend/app/providers/maps/amap.py`、`backend/app/planning/routing.py`、`scheduling.py` | 不复制：当前已有带坐标系、地点 ID、出发时刻、调用上限和错误状态的路线契约。旧模块取同名首条 POI、按直线距离选模式、无期限落盘缓存，会降低身份和时效可靠性。 |
| `PlanAgent/plan.py` 的 `attach_routes` 调用 | `backend/app/planning/scheduling.py` | 不复制：当前排时会把真实路程耗时加入日程，旧调用只在文本计划生成后附加路线。 |
| `frontend/src/components/TripMap.tsx` 路线、虚线、标记和按天筛选 | `frontend/src/pages/AgentWorkbench.tsx` 的 `RouteSketch` 和右侧地图区 | 吸收交互：实际几何为蓝线，未查到路线为灰色虚线；候选卡可定位到所在日，支持底图和路线关系图切换。 |
| `TripMap.tsx` 的 Leaflet 加原始高德瓦片地址 | 当前后端 `/sessions/{id}/map` 静态图 | 暂不复制：当前持有 Web 服务 Key，正式 Web JS API 地图还需要相应的平台 Key 与安全配置。先保持现有底图，未来再接官方前端地图 SDK。 |
| 旧 `/api/plan`、`Orchestrator`、`SearchAgent` 文件 | 当前 `/api/sessions` 运行时及结构化搜索/编辑 | 不整体合并：协议、持久会话与验证循环不同；应按字段和行为逐项迁移。 |

## 本轮可见效果与后续

卡片“在路线图中定位”只切换右侧日期与焦点，不触发重新规划。路线关系图基于当前计划的同一份 `stops/routes`，因此旧计划仍显示旧路线，待新计划完成再同步更新。虚线表示顺序，不代表实际可行交通。

2026-10-01 增量：路线关系图可拖动、缩放，点位可定位右侧行程与中间卡片；同日景点附近 3 公里内最多显示 10 个空心候选点，点击只聚焦卡片，不自动选入计划。后端高德底图仍是静态图片；完整可交互街道地图、地点反选后的修改预览/接受/撤销仍需前端地图 SDK、相应凭据和操作契约。
