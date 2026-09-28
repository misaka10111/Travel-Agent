# Travel-Agent 架构图

本文按当前仓库代码绘制。项目同时包含 Web 应用、一个确定性的旅行生成流水线，以及一个单独的 MCP 搜索 Agent；这些部分并非都接在同一条运行路径上。

## 1. 系统总览

```mermaid
flowchart LR
    User[用户]
    subgraph Web[Web 应用]
        UI[React + TypeScript + Vite<br/>AgentPage / Trips / Profile]
        APIClient[frontend/src/api/client.ts]
    end
    subgraph Backend[FastAPI 后端]
        Routes[REST 路由<br/>/plan /trips /profile /trip-memory 等]
        PlanRoute[POST /api/plan<br/>解析请求并启动编排器]
        ChatRoute[POST /api/agent/chat]
        Placeholder[TravelAgent.chat<br/>当前为占位回复]
        ORM[SQLAlchemy 模型与服务]
        DB[(SQLite 默认\n可配置其他数据库)]
    end
    subgraph Pipeline[旅行计划流水线]
        Orchestrator[Orchestrator<br/>LangGraph 状态图]
        Search[SearchAgent.search.py<br/>结构化并行搜索]
        Plan[PlanAgent.plan.py<br/>并行生成两种风格]
        Validate[ValidateAgent.validate.py<br/>方案审核]
    end
    subgraph Other[独立或可选组件]
        MCP[SearchAgent.agent.py<br/>LangGraph + MCP 工具交互入口]
        Questionnaire[QuestionnaireAgent\n偏好问卷生成脚本]
    end
    subgraph External[外部服务]
        LLM[OpenAI 兼容 LLM API]
        OpenMeteo[Open-Meteo 天气]
        FlyAI[FlyAI CLI<br/>酒店 / 交通 / POI 等]
        Tavily[Tavily 搜索<br/>事件 / 美食 / 网页]
    end

    User --> UI --> APIClient --> Routes
    Routes --> PlanRoute
    PlanRoute --> Orchestrator
    Orchestrator --> Search --> Plan --> Validate
    Validate -.审核不通过且未达上限.-> Plan
    Orchestrator -.可选：按 user_id 读取画像 / 保存行程记忆.-> Routes
    Routes --> ORM --> DB
    Routes --> ChatRoute --> Placeholder
    Search -.自然语言解析 / LLM 工具规划.-> LLM
    Plan --> LLM
    Validate --> LLM
    Questionnaire -.单独使用时.-> LLM
    MCP --> LLM
    MCP -->|stdio MCP 子进程| Search
    Search --> OpenMeteo
    Search --> FlyAI
    Search -.需 Tavily key.-> Tavily
```

## 2. Web 端生成行程的实际调用顺序

```mermaid
sequenceDiagram
    actor U as 用户
    participant UI as frontend AgentPage
    participant Client as frontend API client
    participant API as FastAPI POST /api/plan
    participant Parse as SearchAgent/search.py --parse
    participant Graph as Orchestrator LangGraph
    participant S as SearchAgent/search.py
    participant Tools as SearchAgent/tools.py
    participant P as PlanAgent/plan.py
    participant V as ValidateAgent/validate.py
    participant LLM as OpenAI 兼容 LLM API
    participant DB as 后端数据库

    U->>UI: 输入目的地、日期和偏好
    UI->>UI: 检查缺失字段并逐项追问
    UI->>Client: plan({query, profile, basic})
    Client->>API: POST /api/plan
    opt 只提供自然语言 query
        API->>Parse: 调用 --parse 抽取目的地、日期、预算等
        Parse->>LLM: 结构化解析
        LLM-->>Parse: JSON
        Parse-->>API: 补充后的字段
    end
    API->>Graph: 以 JSON 启动 orchestrator.py
    Graph->>S: search 节点传入目的地、日期、出发地
    S->>Tools: run_search(structured_input)
    par 并发检索
        Tools->>LLM: 事件 / 美食等 LLM 辅助查询
        Tools->>Tools: 天气、酒店、景点、优惠
        Tools->>Tools: 有出发地时查询往返机票和火车
    end
    Tools-->>S: 结构化搜索结果
    S-->>Graph: search 状态
    Graph->>P: 画像、搜索结果、基础信息、作答
    par 生成两种方案
        P->>LLM: 经典人气方案
        P->>LLM: 小众深度方案
    end
    LLM-->>P: 两种逐日行程
    P-->>Graph: plan 状态
    Graph->>V: 计划 + 搜索依据 + 用户偏好
    V->>LLM: 审核预算、交通、偏好、天气、时间和完整性
    LLM-->>V: passed / issues / feedback
    V-->>Graph: audit 与 history
    alt 未通过且尚可继续
        Graph->>P: 携带 feedback 重新生成
    else 通过或达到轮数上限
        Graph-->>API: 搜索结果、最终计划、审核历史
    end
    API-->>Client: JSON 响应
    Client-->>UI: 展示方案卡片与逐日行程
    UI-->>U: 可比较的旅行计划
```

Orchestrator 直接从命令行接收含 `user_id` 的 JSON 时，会通过 `BACKEND_URL` 读取画像并保存最终行程记忆；当前 `/api/plan` 的请求模型没有 `user_id` 字段，因此 Web 端这条调用路径不会触发这两个可选步骤。

## 3. Orchestrator 状态图和各节点数据

```mermaid
flowchart TD
    Start([START]) --> Search[search_node<br/>输入 destination / dates / origin<br/>调用 SearchAgent/search.py]
    Search --> Plan[plan_node<br/>输入 profile / search / basic / answers<br/>可附 feedback]
    Plan --> Validate[validate_node<br/>检查计划并记录 audit / history]
    Validate --> Decision{passed 或 iteration >= MAX_ITERATIONS?}
    Decision -->|是| End([END])
    Decision -->|否| Plan
```

当前 `MAX_ITERATIONS = 1`，即首次生成后审核一次；若未通过，条件边也会结束。因此当前值下不会发生第二次携带 feedback 的重生成。`ValidateAgent/run.py` 有自己的独立循环实现，但 Orchestrator 调用的是 `validate.py`，两者不是同一个入口。

| 节点 | 输入 | 输出 / 副作用 |
| --- | --- | --- |
| `search` | `destination`、`start_date`、`end_date`、可选 `basic.origin` | `search` 结构化结果；并发查天气、酒店、景点、优惠、活动和美食，有出发地时再查往返机票与火车 |
| `plan` | `profile`、`search`、`basic`、`answers`、可选 `feedback` | 两个不同风格的行程方案和行程块；两个风格并发生成 |
| `validate` | `plan`、`profile`、`search`、`basic`、`answers` | `audit`、审核 `history` 和给计划生成器的 `feedback` |

## 4. SearchAgent 内部结构

```mermaid
flowchart LR
    Input[JSON 或自然语言]
    Parse[search.py\n自然语言 -> JSON]
    Run[tools.run_search\n确定性综合检索]
    Pool[ThreadPoolExecutor\n并行执行各检索任务]
    Weather[Open-Meteo]
    Travel[FlyAI CLI\n酒店 / 航班 / 火车 / POI / 优惠]
    Web[Tavily\n网页 / 活动 / 美食]
    LLM[LLM API\n解析和部分结果提取]
    Result[结构化 JSON]
    ReAct[agent.py\nLangGraph ReAct]
    MCPClient[langchain-mcp-adapters]
    MCPServer[tools.py MCP stdio server]

    Input --> Parse --> LLM
    Parse --> Run --> Pool
    Pool --> Weather
    Pool --> Travel
    Pool --> Web
    Travel -.部分数据归一化.-> LLM
    Web -.查询与整理.-> LLM
    Weather --> Result
    Travel --> Result
    Web --> Result
    ReAct --> MCPClient -->|stdio 子进程| MCPServer
    ReAct --> LLM
    MCPServer --> Weather
    MCPServer --> Travel
    MCPServer --> Web
```

`Orchestrator.search_node` 使用 `search.py` 的 JSON/CLI 入口，直接运行并行检索。`agent.py` 则提供可交互的 ReAct + MCP 工具调用入口；当前 Orchestrator 没有调用 `agent.py`。

## 5. 仓库中的其他组件与边界

```mermaid
flowchart TB
    Frontend[React 前端]
    Backend[FastAPI 后端]
    Database[(SQLite / 配置的数据库)]
    Orchestrator[Orchestrator]
    Questionnaire[QuestionnaireAgent]
    Chat[POST /api/agent/chat]
    Stub[TravelAgent.chat 占位回复]
    Compose[docker-compose.yml]

    Frontend -->|/api/plan| Backend --> Orchestrator
    Frontend -->|账户 / 画像 / 行程 CRUD| Backend --> Database
    Frontend -.当前 AgentPage 不走该接口.-> Chat --> Stub
    Questionnaire -.当前编排图未接入.-> Orchestrator
    Compose -->|仅定义 backend 与 frontend 容器| Backend
    Compose --> Frontend
    Backend -.计划路由仍引用仓库根目录下的 Orchestrator / 各 Agent 脚本.-> Orchestrator
```

- 前端 `AgentPage` 的生成按钮调用 `/api/plan`，不是 `/api/agent/chat`。聊天接口当前只返回占位文字。
- `QuestionnaireAgent` 已实现独立问卷生成，但当前 Orchestrator 图和 `/api/plan` 路由都没有调用它。
- `docker-compose.yml` 只定义 frontend 和 backend。Agent 脚本不是独立容器；后端计划路由以仓库根目录为基准查找 Orchestrator 和各 Agent，并要求对应 Python 环境存在。因此容器部署需要额外提供这些目录和解释器环境。
- Orchestrator 和后端计划路由写死了 `.venv/bin/python` 路径；这适用于类 Unix 环境，Windows 本地运行需要调整解释器路径或兼容逻辑。
- 可选的 user profile 与 trip memory 流程通过 `BACKEND_URL` 回调 FastAPI；普通计划请求不带 `user_id` 时不会读取或保存这部分数据。
