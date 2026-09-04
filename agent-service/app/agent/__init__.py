"""Agent 聊天 Agent 模块。

Phase 1：一个 Pydantic AI Agent + 3 个核心工具（读画布 / 改 prompt / 触发任务），
通过 Java API 与 canvas_project / task 表打交道。

设计原则：
- 不侵入现有 pipeline（graph / nodes / scheduler 保持不动）
- 工具直接 HTTP 调 Java，agent-service 内不重复维护画布数据
- 我能直接 curl /v1/agent/chat 使用，用户前端同样调
"""
