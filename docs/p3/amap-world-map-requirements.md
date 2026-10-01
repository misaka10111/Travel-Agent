# 高德交互地图与世界地图接入条件

核对日期：2026-10-01。国内 Web JS 交互地图已接入 `/agent`，与原静态底图、路线关系图并列；海外地图和路线仍未授权或验证。

## 国内交互底图

1. 登录 [高德开放平台控制台](https://console.amap.com/dev/key/app)管理 **Web端（JS API）** Key 和安全密钥。现有 `AMAP_WEB_SERVICE_KEY` 继续负责后端国内地点与路线查询，不能充当 JS API Key。
2. 本机将公开的 JS Key 放在被 Git 忽略的 `frontend/.env.local`：`VITE_AMAP_JS_KEY=...`；将配套安全密钥放在被 Git 忽略的 `backend/.env`：`AMAP_JS_SECURITY_CODE=...`。不能把安全密钥放在 `VITE_*` 中或提交到仓库。前端用官方 [JS API Loader](https://lbs.amap.com/api/javascript-api-v2/guide/abc/load)；`/_AMapService` 经 Vite 转发到后端，按[官方安全配置](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)在服务端追加安全密钥。
3. 交互地图按日显示 GCJ-02 行程点、附近候选与已查询路线；待查询段用灰色虚线，点选地图标记或行程卡片只更新焦点，不触发重排。地图加载失败时退回路线关系图。部署时还需配置正式域名、同源代理、访问控制与配额监控。

## 世界地图与国外行程

- 高德 [JS API 世界地图](https://lbs.amap.com/api/javascript-api-v2/guide/map/world-map) 是高级能力：Web端 JS API Key 之外，需要提交[官方工单](https://console.amap.com/dev/ticket/create/66)申请商务授权；授权后才能设置 `showOversea: true`。仅开启这个图面能力不等于后台已能规划国外路线。
- 国外地点检索及路线属于另一个授权范围。[海外地点服务](https://lbs.amap.com/api/web-service/guide/searchs)与[海外路线服务](https://lbs.amap.com/api/web-service/guide/routes)要求 Web 服务 Key 的海外权限，官方标明为高级/商业服务。海外地点返回 WGS84 坐标；当前 `AmapProvider` 明确只接受中国大陆 GCJ-02，必须在授权、配额和坐标契约明确后增加独立适配与实际验证。
- 申请工单时应确认：目标国家和城市、地点检索/详情/周边、步行/公交/驾车路线各自权限、JS 世界地图图面权限、QPS/日额度、费用和商用条件。先用新加坡少量地点和有方向的路段验证，再扩到其他国家。

## 当前验证边界

本轮国内真实调用：北京两个地点返回坐标；公交路段 `status=ok`，约 73 分钟、127 个折线点；静态地图返回 HTTP 200 图片。第一次串行探测第二次请求曾出现 `unavailable`，重试成功，不能据此保证供应商始终稳定。新增浏览器验证：北京交互街道底图、行程标记、路线和双向点选正常显示；独立一日游用时约 29 秒生成可见草案。该草案把未写年份的“10月8日”解释成已过去的 2025-10-08，属于独立的日期解析缺陷，不能据此认定日期规划正确。世界地图授权和海外行程仍未验证。
