# 高德交互地图与世界地图接入条件

核对日期：2026-10-01。当前项目只有服务端 `AMAP_WEB_SERVICE_KEY`，前端 `/agent` 提供静态高德底图及可拖动的路线关系图，尚未加载高德地图 JS API。

## 国内交互底图

1. 登录 [高德开放平台控制台](https://console.amap.com/dev/key/app)，在「应用管理 → 我的应用」中选择或创建应用，新增服务平台为 **Web端（JS API）** 的 Key，并取得配套安全密钥。现有 Web 服务 Key 不能充当 JS API Key。官方步骤：[JS API 准备](https://lbs.amap.com/api/javascript-api-v2/prerequisites)。
2. 开发域名和日后正式域名应分别配置并核对可访问性。项目接入时使用官方 [JS API Loader](https://lbs.amap.com/api/javascript-api-v2/guide/abc/load)，不要复制 `route-map` 分支的原始瓦片 URL。
3. 安全密钥优先按[官方安全配置](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)放在服务端代理，避免写入 Git 或公开前端打包产物。提供 Key 后需实现前端地图实例、按天标记和路线绘制，并保持坐标系与供应商一致。

## 世界地图与国外行程

- 高德 [JS API 世界地图](https://lbs.amap.com/api/javascript-api-v2/guide/map/world-map) 是高级能力：Web端 JS API Key 之外，需要提交[官方工单](https://console.amap.com/dev/ticket/create/66)申请商务授权；授权后才能设置 `showOversea: true`。仅开启这个图面能力不等于后台已能规划国外路线。
- 国外地点检索及路线属于另一个授权范围。[海外地点服务](https://lbs.amap.com/api/web-service/guide/searchs)与[海外路线服务](https://lbs.amap.com/api/web-service/guide/routes)要求 Web 服务 Key 的海外权限，官方标明为高级/商业服务。海外地点返回 WGS84 坐标；当前 `AmapProvider` 明确只接受中国大陆 GCJ-02，必须在授权、配额和坐标契约明确后增加独立适配与实际验证。
- 申请工单时应确认：目标国家和城市、地点检索/详情/周边、步行/公交/驾车路线各自权限、JS 世界地图图面权限、QPS/日额度、费用和商用条件。先用新加坡少量地点和有方向的路段验证，再扩到其他国家。

## 当前验证边界

本轮国内真实调用：北京两个地点返回坐标；公交路段 `status=ok`，约 73 分钟、127 个折线点；静态地图返回 HTTP 200 图片。第一次串行探测第二次请求曾出现 `unavailable`，重试成功，不能据此保证供应商始终稳定。没有 JS API Key 或世界地图授权，无法验证交互底图、海外图面和海外行程。
