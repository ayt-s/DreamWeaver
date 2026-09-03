# DreamWeaver — AI 短视频创作 Agent

> 用户一句话需求 → AI 导演 Agent 自动产出短视频成片。
> 「剧本 → 分镜 → 提示词 → 素材生成 → 视频生成 → 质检 → 修正」完整 Agent 闭环。

## ✨ 项目亮点

- **多 Agent 协作编排**：LangGraph 状态图驱动的创作流水线（需求解析 → 剧本 → 分镜 → 生成 → 质检），节点状态全量落库，支持断点恢复
- **工具调用与审计**：MCP 风格工具注册模式，工具调用全链路 trace（参数/耗时/结果/重试），前端实时可视化 Agent 思考过程
- **评测闭环**：双层 QC（规则 + LLM 判分）+ 失败回流 + 样本回放，改 Prompt 先过评测集
- **异步解耦**：FastAPI 异步任务 + video_id 轮询 + Java 端乐观锁幂等回调，三端契约统一
- **真实产品级细节**：429/5xx 指数退避重试、Redis 分布式锁、断点恢复、环境变量密钥隔离

## 🛠 技术栈

### 模型侧 — Python 3.11 / FastAPI
| 领域 | 技术 |
|---|---|
| Agent 编排 | LangGraph（状态机 DAG、interrupt 挂起、断点恢复） |
| Web 框架 | FastAPI + Uvicorn（异步、SSE 支持） |
| 模型网关 | Agnes AI API（文本 `agnes-2.5-flash` / 图像 `agnes-image-2.5-flash` / 视频 `agnes-video-2.5-flash`+`2.5`） |
| 网络层 | httpx（异步客户端、指数退避重试） |
| 校验/配置 | Pydantic（结构化输出）、环境变量隔离（`.env.example`） |
| 测试 | pytest + pytest-asyncio |

### 业务侧 — Java 17 / Spring Boot 3
| 领域 | 技术 |
|---|---|
| Web 层 | Spring MVC（controller/service 分层） |
| 持久层 | MyBatis-Plus（乐观锁 `@Version`、LambdaQueryWrapper） |
| 数据库 | MySQL 8（utf8mb4） |
| HTTP 客户端 | WebFlux WebClient（调用 FastAPI） |
| 校验/转换 | Jakarta Validation、Lombok、Jackson |
| 构建 | Maven 3.9 + 项目自带 `mvnw`（Windows git-bash 兼容） |
| 异步回调 | `/internal/notify` + 状态机转移表 + 乐观锁幂等 |

### 前端 — React 18 / TypeScript
| 领域 | 技术 |
|---|---|
| 构建 | Vite 5 + TypeScript（严格模式） |
| UI | Tailwind CSS + framer-motion（动画）+ lucide-react（图标） |
| 状态管理 | TanStack Query（服务端状态/轮询）+ Zustand（全局状态） |
| 表单 | React Hook Form + 校验 |
| 通信 | Axios（统一 client + CommonResult 解包）、SSE（EventSource） |
| 路由 | React Router 6 |
| 测试 | Vitest + Testing Library |
| 代码质量 | ESLint 9 + Prettier |

### 部署形态
```
React (5173) ──proxy──▶ Spring Boot (8080) ──REST──▶ FastAPI (8000) ──HTTPS──▶ Agnes AI API
                                    ▲                                   │
                                    └────── /internal/notify 回调 ◀──────┘
```

## 🚀 快速开始

### 1. 配置环境变量（必需）

```bash
cd agent-service
cp .env.example .env
# 编辑 .env，填入你的 Agnes API Key：
# 获取地址：https://platform.agnes-ai.com/ → Settings → API Keys
```

### 2. 启动模型侧（agent-service）

```bash
cd agent-service
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

### 3. 启动业务侧（web-backend）

```bash
# 需要 MySQL（建表脚本：web-backend/src/main/resources/db/init.sql）
./mvnw spring-boot:run
```

### 4. 启动前端（web-frontend）

```bash
cd web-frontend
npm install
npm run dev   # 打开 http://localhost:5173
```

## 📁 项目结构

```
├── agent-service/   # FastAPI + LangGraph（Agent 编排、模型网关、轮询、回调）
│   ├── app/
│   │   ├── gateway/     # Agnes API 网关（唯一接触第三方的地方）
│   │   ├── tools/       # Agent 工具（MCP 风格，供节点调用）
│   │   ├── nodes/       # LangGraph 节点（一文件一节点）
│   │   ├── callback/    # FastAPI → Java 回调通知
│   │   ├── graph.py     # 图装配（只描述节点/边）
│   │   ├── state.py     # State 类型定义
│   │   └── main.py      # FastAPI 入口 + 路由
│   └── tests/           # pytest 冒烟 + 契约测试
├── web-backend/     # Spring Boot（用户/任务/资产/配额、回调接收）
│   └── src/main/java/com/dreamweaver/
│       ├── controller/  # REST 层（只做参数接收+响应组装）
│       ├── service/     # 业务接口 + impl（编排逻辑）
│       ├── mapper/      # MyBatis-Plus Mapper
│       ├── entity/      # 数据库实体
│       ├── dto/         # 请求/响应对象（禁止 entity 出参）
│       ├── common/      # 统一返回体
│       └── config/      # WebClient、MyBatis-Plus 配置
├── web-frontend/    # React（创作工作台、轨迹可视化、画廊）
│   └── src/
│       ├── api/         # Axios 封装（统一解包 CommonResult）
│       ├── components/  # 可复用组件
│       ├── pages/       # 页面级组件
│       ├── hooks/       # 自定义 hooks（SSE 订阅等）
│       ├── store/       # Zustand 全局状态
│       └── types/       # TS 类型（与后端 dto 对齐）
├── docs/            # 架构方案、编排设计、工程规范
└── mvnw             # git-bash Maven wrapper
```

## ✅ 测试

```bash
# Python
cd agent-service && python -m pytest tests/ -v

# Java
cd web-backend && ./mvnw test

# 前端
cd web-frontend && npm run test && npm run lint
```

## 📚 设计文档

- 架构方案：`docs/DreamWeaver-架构方案-v2-Agent.md`
- LangGraph 编排与断点恢复：`docs/DreamWeaver-LangGraph编排设计.md`
- 工程规范：`docs/DreamWeaver-工程规范.md`
- 状态机回调设计：`docs/references/state-machine-callback-design.md`

## 🔒 安全说明

- API Key 只存于 `agent-service/.env`（已 gitignore），前端与 Java 均不接触
- 密钥全部环境变量注入，`.env.example` 提供模板与获取指引
- 回调幂等：乐观锁 + 状态机转移表 + 终态检查三重防护