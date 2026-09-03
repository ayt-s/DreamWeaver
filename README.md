# DreamWeaver — AI 短视频创作 Agent

> 用户一句话需求 → AI 导演 Agent 自动产出短视频成片。
> 「剧本 → 分镜 → 提示词 → 素材生成 → 视频生成 → 质检 → 修正」完整 Agent 闭环。

## 技术栈

- **前端**：React (Vite + TS)
- **业务侧**：Java 17 + Spring Boot 3（controller/service/impl/mapper/entity 分层）
- **模型侧**：Python 3.11 + FastAPI + LangGraph（Agent 编排）
- **模型**：Agnes AI（`agnes-2.5-flash` 文本 / `agnes-image-2.5-flash` 图像 / `agnes-video-2.5-flash`+`agnes-video-2.5` 视频）

## 快速开始

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
# Maven 环境：项目自带 mvnw wrapper（git-bash 下直接可用）
./mvnw spring-boot:run
```

### 4. 前端（web-frontend，待建）

```bash
cd web-frontend
npm install && npm run dev
```

## 项目结构

```
├── agent-service/   # FastAPI + LangGraph（Agent 编排、模型网关、轮询）
├── web-backend/     # Spring Boot（用户/任务/资产/配额）
├── web-frontend/    # React（创作工作台、轨迹可视化、画廊）
├── docs/            # 架构方案、编排设计、工程规范
└── mvnw             # git-bash Maven wrapper
```

## 测试

```bash
cd agent-service
python -m pytest tests/ -v
```

## 设计文档

- 架构方案：`docs/DreamWeaver-架构方案-v2-Agent.md`
- LangGraph 编排与断点恢复：`docs/DreamWeaver-LangGraph编排设计.md`
- 工程规范：`docs/DreamWeaver-工程规范.md`