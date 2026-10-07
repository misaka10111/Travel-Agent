# TravelAgent

一个支持自由输入、按需追问、周边餐厅推荐和行程修改的旅行规划应用：

- **前端**：React 18 + TypeScript + Vite + React Router
- **后端**：Python 3.13 + FastAPI + SQLAlchemy 2 + Pydantic v2
- **数据**：默认使用 SQLite（开发环境），可无缝切换为 PostgreSQL/MySQL
- **Agent**：QuestionAgent 提取需求，SearchAgent 搜索，PlanAgent 排行程并补餐厅，ValidateAgent 审核

## 使用流程

1. 在 AI 助手中描述旅行想法，例如“从上海去杭州玩2天，2人，总预算4000元，想逛西湖、吃杭帮菜”。
2. 系统保留已知信息，只询问缺少的必要字段；问卷支持建议选项和手动填写。确认信息后开始规划。
3. 打开一个方案查看每天安排。右侧候选可选择日期和时间，直接加入行程；餐厅和酒店优先替换当天已有推荐。
4. 点击行程中的条目，再在聊天框提出修改要求，例如“换一家便宜一点、步行能到的餐厅”。系统只修改选中的条目，并重算费用和地图路线。
5. 确认方案后可保存；登录用户的行程会保存到历史记录。

餐厅在景点排好之后，围绕实际午餐/晚餐相邻的景点搜索。排序权重为距离 70%、评分 20%、餐饮预算 10%，距离越远得分衰减越快。每个景点锚点先对排名前 8 家候选核实高德步行路线，再用 `max(直线距离, 实际步行距离)` 计算距离权重；超过 5 公里或没有可信坐标的候选不进入自动推荐。附近候选品质接近时优先不同餐厅，附近只有合适的一家时允许重复。

候选和行程分别标明直线距离与已核实的步行米数、分钟数；步行接口失败时退回明确标注的直线距离，不推算步行时间。餐厅保留详情/地图链接，用户手动选择的餐厅会保留。餐饮预算分摊只用于排序，不是用户的单餐硬上限。

费用按单个方案、实际人数估算；未报价项目会标明，不能把已知费用小计当作完整旅行总价。审核保留优化建议，仅对有证据、影响执行且可修复的严重问题反馈重规划一次；报价待核实或休闲留白不会触发反复重跑。

首次生成会根据地图返回的交通耗时补上转场缓冲，避免餐后立即开始远处景点。局部修改保留未选条目的时间；若新路线无法容纳，会提示调整时间。

## 目录结构

```text
TravelAgent/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py          # 应用入口（CORS、路由注册、建表）
│   │   ├── config.py        # 配置（pydantic-settings）
│   │   ├── db.py            # SQLAlchemy engine / session
│   │   ├── models/          # ORM 模型
│   │   ├── schemas/         # Pydantic 请求/响应模型
│   │   ├── api/routes/      # 路由层（health / trips / destinations / agent）
│   │   ├── services/        # 业务逻辑与数据初始化
│   │   └── agent/           # 基础聊天接口
│   └── tests/               # pytest 测试
├── frontend/                # React 前端
│   └── src/
│       ├── api/             # API 客户端与类型定义
│       ├── components/      # 通用组件
│       ├── pages/           # 页面
│       ├── hooks/           # 自定义 hooks
│       └── styles/          # 全局样式
├── QuestionAgent/           # 自由输入识别与缺项问卷
├── SearchAgent/             # 旅行资源及高德周边餐厅搜索
├── PlanAgent/               # 规划、餐厅排序、局部修改和路线
├── ValidateAgent/           # 方案审核
└── Orchestrator/            # 流程编排与流式进度
```

## 快速开始

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

后端默认运行在 http://localhost:8000 ，接口文档见 http://localhost:8000/docs 。

各 Agent 使用对应目录下的 `.venv`。首次运行还需为 `SearchAgent`、`PlanAgent`、`ValidateAgent`、`Orchestrator` 创建环境并安装各自 `requirements.txt`，在 `SearchAgent` 目录运行 `npm install` 安装搜索 CLI 依赖。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认运行在 http://localhost:5173 ，开发服务器会把 `/api` 请求代理到后端。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| POST | `/api/question` | 提取需求、按需追问与修改意图 |
| POST | `/api/plan/stream` | 搜索、规划、审核的流式进度和结果 |
| POST | `/api/plan` | 生成或加入、删除、修改已有行程 |
| GET/POST | `/api/destinations` | 目的地列表 / 新增目的地 |
| GET/POST | `/api/trips` | 行程列表 / 新建行程 |
| GET/PATCH/DELETE | `/api/trips/{id}` | 查看 / 更新 / 删除行程 |
| POST | `/api/agent/chat` | AI 旅行助手对话 |

## 配置

后端通过环境变量或 `backend/.env` 配置（参考 `backend/.env.example`）：

- `DATABASE_URL`：数据库连接串，默认 `sqlite:///./travelagent.db`
- `CORS_ORIGINS`：允许跨域的前端来源
- `API_PREFIX`：接口前缀，默认 `/api`
- `TRAVEL_AGENT_DEBUG`：后端调试开关，默认 `false`

前端通过 `frontend/.env` 配置 `VITE_API_BASE_URL`（默认 `/api`，走开发代理）。

模型配置放在各 Agent 的本地 `.env`：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`；QuestionAgent 默认复用 PlanAgent 的配置。高德 Web 服务密钥通过 `AMAP_KEY` 配置给 SearchAgent 和 PlanAgent。密钥文件不要提交到仓库。

当前默认使用 `qwen3.8-27b`，问卷、规划和审核均关闭模型长思考以减少等待。文本向量模型用于检索，不能替代这些文本生成调用。

## 运行测试

```bash
cd backend
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Agent 的重点回归测试可分别在对应目录运行：

```bash
cd QuestionAgent
../PlanAgent/.venv/bin/python -m unittest test_trip_intent test_question_response
cd ../SearchAgent
.venv/bin/python -m unittest test_dining_agent
cd ../PlanAgent
.venv/bin/python -m unittest test_dining_plan test_walking_dining test_route_timing test_plan_state test_trip_changes test_schedule test_price_sources
cd ../ValidateAgent
.venv/bin/python -m unittest test_validate
cd ../Orchestrator
.venv/bin/python -m unittest test_audit_context test_audit_flow
```

SearchAgent 的真实地图接口测试默认跳过，可显式设置 `RUN_AMAP_INTEGRATION_TESTS=1` 运行。前端使用 `npm run build` 验证类型和构建。

启动前后端后，可在仓库根目录运行 `backend/.venv/bin/python scripts/verify_live_flow.py`，用本地配置的模型和地图接口验证缺项问卷、生成行程、加入餐厅和选中修改。测试不创建用户历史行程，结果保存在 `/private/tmp/travel-agent-live-flow.json`。

## 性能优化

多 Agent 生成流水线的加速优化记录、关键配置参数与待实施方案见
[OPTIMIZATION.md](./OPTIMIZATION.md)。
