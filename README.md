# DreamWeaver — AI 短视频创作 Agent

> 用户一句话需求 → AI 导演 Agent 自动产出短视频成片。
> 「剧本 → 分镜 → 提示词 → 素材生成 → 视频生成 → 质检 → 修正」完整 Agent 闭环。

## 🚀 快速开始

### 0. 前置依赖

- Python ≥ 3.11、Java 17、Node ≥ 18
- MySQL 8 —— 建库 `dreamweaver`，建表脚本 `web-backend/src/main/resources/db/init.sql`
- Redis —— **可选**：用于 agent 会话快照与 Java 看门狗；agent 侧连不上会静默降级为纯内存，不阻塞启动

### 1. 配置环境变量（必需）

```bash
cd agent-service
cp .env.example .env
# 编辑 .env，至少填两项：
#   AGNES_API_KEY   获取地址：https://platform.agnes-ai.com/ → Settings → API Keys
#   JAVA_NOTIFY_URL Phase 2 回调目标，如 http://localhost:8080（不填则不回写 Java 侧状态）
# 可选：AGNES_API_KEY_cn 走国内端点（同家供应商多账号扩容）
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
cd web-backend
./mvnw spring-boot:run
```

### 4. 启动前端（web-frontend）

```bash
cd web-frontend
npm install
npm run dev   # 打开 http://localhost:5173
```
## 🔒 安全说明

- API Key 只存于 `agent-service/.env`（已 gitignore），前端与 Java 均不接触
- 密钥全部环境变量注入，`.env.example` 提供模板与获取指引
- 回调幂等：乐观锁 + 状态机转移表 + 终态检查三重防护
- 回调失联不丢数据：本地 JSONL 兜底 + 结构化补拉端点，Java 恢复后主动 `sync-fallback`

## ✨ 项目亮点

- **多 Agent 协作编排**：LangGraph 状态图驱动的创作流水线（需求解析 → 剧本 → 分镜 → 生成 → 质检 → 修正），每个 checkpoint 全量写入 Redis 快照，进程重启自动恢复活跃会话
- **工具调用与审计**：MCP 风格工具层（`app/tools/`），state 只留 `{node, status, elapsed_ms}` 极简轨迹（结构性防膨胀）+ SSE 推前端画时间线，LLM 出口级细节交给 LangSmith（默认关闭、零开销）
- **质检与自愈闭环**：双层 QC（OpenCV 抽帧规则层判黑帧/模糊 + LLM 判分）+ `fix_looping` 失败镜定向重生（修正后缀按原因映射，`random50` 分组做在线 A/B），不留「修不好就整片重跑」
- **异步解耦**：FastAPI 有界并发调度队列（超出 FIFO 排队）+ `video_id` 轮询 + Java 端 `@Version` 乐观锁幂等回调；回调失败落本地 JSONL，Java 起来后主动补拉
- **真实产品级细节**：429/503 指数退避 ± 抖动、双端点（国际/国内）按 session 粘性路由 + 提交失败 failover、Redisson TTL 看门狗 + 自动重试、密钥全环境变量隔离

## 🛠 技术栈

### 模型侧 — Python 3.11 / FastAPI
| 领域 | 技术 |
|---|---|
| Agent 编排 | LangGraph（状态图 DAG、条件边、checkpoint 全量落库、断点恢复） |
| 对话/文稿 Agent | pydantic-ai-slim（创作对话、小说→分镜预处理） |
| Web 框架 | FastAPI + Uvicorn（异步、SSE 断线重连补漏） |
| 模型网关 | Agnes AI API（文本 `agnes-2.5-flash` / 图像 `agnes-image-2.5-flash` / 视频 `agnes-video-2.5-flash`+`2.5`），多端点池 + failover |
| 网络层 | httpx（异步客户端、指数退避 + 抖动重试） |
| 质检/媒体 | OpenCV（抽帧黑帧、模糊检测）、imageio-ffmpeg（本地兜底合成） |
| 会话/调度 | Redis 快照 + 心跳续期、有界并发 SessionScheduler（Redis 不可用静默降级） |
| 可观测性 | LangSmith（裸 httpx 调用需手动 tracing，默认关闭）+ 轮转文件日志（`data/logs/agent.log`） |
| 校验/配置 | Pydantic（结构化输出）、环境变量隔离（`.env.example`） |
| 测试 | pytest + pytest-asyncio |

### 业务侧 — Java 17 / Spring Boot 3
| 领域 | 技术 |
|---|---|
| Web 层 | Spring MVC（controller/service 分层） |
| 持久层 | MyBatis-Plus（乐观锁 `@Version`、LambdaQueryWrapper） |
| 数据库 | MySQL 8（utf8mb4） |
| 缓存/看门狗 | Redisson（`RMapCache` 任务 TTL 看门狗、`RBucket` 图片缓存 + 重试计数窗口） |
| HTTP 客户端 | WebFlux WebClient（调用 FastAPI） |
| 校验/转换 | Jakarta Validation、Lombok、Jackson |
| 构建 | Maven 3.9 + 项目自带 `mvnw`（Windows git-bash 兼容） |
| 异步回调 | `/internal/notify` + 状态机转移表 + 乐观锁幂等 + `/internal/heartbeat` 续期 |

### 前端 — React 18 / TypeScript
| 领域 | 技术 |
|---|---|
| 构建 | Vite 5 + TypeScript（严格模式） |
| UI | Tailwind CSS + framer-motion（动画）+ lucide-react（图标） |
| 画布 | React Flow（`@xyflow/react` 无限画布分段编排） |
| 状态管理 | TanStack Query（服务端状态/轮询）+ Zustand（全局状态） |
| 表单 | React Hook Form + 校验 |
| 通信 | Axios（统一 client + CommonResult 解包）、SSE（EventSource） |
| 路由 | React Router 6 |
| 测试 | Vitest + Testing Library |
| 代码质量 | ESLint 9 + Prettier |

### 部署形态
```
React (5173)
  ├── /api/* ──▶ Spring Boot (8080) ──REST──▶ FastAPI (8000) ──HTTPS──▶ Agnes AI API
  │                    ▲                                   │
  │                    └── /internal/notify、/internal/heartbeat 回调 ◀──┘
  └── /v1/*  ──▶ FastAPI (8000)          # SSE 轨迹事件直连（Vite proxy）

旁路依赖：MySQL 8（业务数据）· Redis（会话快照 / TTL 看门狗）· 本地 ffmpeg（兜底合成）
```


## 📁 项目结构

```
├── agent-service/   # FastAPI + LangGraph（Agent 编排、模型网关、轮询、回调）
│   ├── app/
│   │   ├── gateway/       # Agnes API 网关（唯一接触第三方的地方，多端点池 + failover）
│   │   ├── tools/         # Agent 工具（MCP 风格注册：image / video / qc）
│   │   ├── nodes/         # LangGraph 节点（一文件一节点）
│   │   ├── agent/         # 创作对话 Agent（pydantic-ai）
│   │   ├── novel/         # 小说/文稿 → 分镜预处理（analyzer/splitter/storyboarder）
│   │   ├── controller/    # 内部 API（sync-fallback、novel 预处理）
│   │   ├── callback/      # FastAPI → Java 回调通知
│   │   ├── graph.py       # 图装配（只描述节点/边）
│   │   ├── state.py       # State 类型定义
│   │   ├── scheduler.py   # 有界并发 + FIFO 排期
│   │   ├── poller.py      # 视频任务独立轮询
│   │   ├── session_store.py / recovery.py  # Redis 快照 + 启动恢复
│   │   ├── fallback.py    # 回调失败本地 JSONL 兜底
│   │   ├── events.py      # SSE 事件缓冲（支持断线重连补漏）
│   │   └── main.py        # FastAPI 入口 + 路由
│   └── tests/             # pytest：30 个测试模块（冒烟 + 契约 + 回归）
├── web-backend/     # Spring Boot（用户/任务/资产/配额/画布、回调接收）
│   └── src/main/java/com/dreamweaver/
│       ├── controller/  # REST 层（只做参数接收+响应组装）
│       ├── service/     # 业务接口 + impl（编排逻辑、看门狗、自动重试）
│       ├── mapper/      # MyBatis-Plus Mapper
│       ├── entity/      # 数据库实体
│       ├── dto/         # 请求/响应对象（禁止 entity 出参）
│       ├── common/      # 统一返回体 + 全局异常处理
│       └── config/      # WebClient、MyBatis-Plus、CORS、AgentServiceProperties
├── web-frontend/    # React（创作工作台、轨迹时间线、画廊、图生视频、小说）
│   └── src/
│       ├── api/         # Axios 封装（统一解包 CommonResult）
│       ├── components/  # 可复用组件（轨迹面板、分段管理、批量返工）
│       ├── pages/       # 页面级组件（Create / Gallery / ImageVideo / Novel）
│       ├── hooks/       # 自定义 hooks（SSE 订阅等）
│       ├── store/       # Zustand 全局状态
│       ├── types/       # TS 类型（与后端 dto 对齐）
│       └── utils/       # 纯函数工具
├── docs/            # 架构方案、编排设计、工程规范、进度报告
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

- 架构方案：`docs/DreamWeaver-架构方案-v2-Agent.md`（Agent 版）；`docs/DreamWeaver-架构方案.md`（v1 概览）
- LangGraph 编排与断点恢复：`docs/DreamWeaver-LangGraph编排设计.md`
- 工程规范：`docs/DreamWeaver-工程规范.md`
- 架构审查：`docs/DreamWeaver-架构审查.md`
- 状态机回调设计：`docs/references/state-machine-callback-design.md`
- 分阶段进度：`docs/phase2-progress-report.md`、`docs/phase3-delegation-summary.md`、`docs/final-progress-report.md`
- ⚠️ 文档中的「评测集 + 失败样本回放」为**规划项**，尚未落地（当前已实现的是双层 QC + `fix_looping` 失败回流 + 修正后缀在线 A/B）


