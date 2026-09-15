@echo off
REM ============================================================================
REM DreamWeaver agent-service 开发启动脚本
REM
REM ⚠️ --reload-dir app 是必须的，不要去掉：
REM    uvicorn 默认递归监视**整个工作目录**的 *.py，**包括 tests/**。
REM    于是「跑真实任务时改任何 .py、或跑测试新增 .py」都会重启服务 →
REM    在跑的会话被杀 → recovery 用同一 session_id 重跑 → 状态重置 +
REM    重新烧一遍 agnes 额度。（2026-09-15 实测踩到）
REM
REM    收窄到 app/ 后：改 app 下的代码仍会自动重载；改 tests/scripts/data/
REM    不会再打断正在跑的任务。
REM
REM 日志：应用自身写 agent-service\data\logs\agent.log（10MB × 5 份轮转）。
REM       额外落一份 stdout 到这个控制台。
REM ============================================================================
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app --port 8000
