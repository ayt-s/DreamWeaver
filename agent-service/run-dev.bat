@echo off
REM ============================================================================
REM DreamWeaver agent-service 开发启动脚本
REM
REM ⚠️ 用**纯 --reload**，不要加 --reload-dir！
REM
REM 2026-09-15 实测（隔离实例对照，端口 8001/8004）：
REM   ✓ 纯 --reload（监视整个 agent-service）       → 改 app/xxx.py 内容**会**重载
REM   ✗ --reload --reload-dir app                    → 改 app/xxx.py 内容**不会**重载
REM                                                     （只有新增/删除文件才触发）
REM
REM   即 --reload-dir 在本机（Windows + watchfiles 1.2.0）对「已有文件的内容变更」
REM   失效，表现为「启动日志说在监视、改代码却毫无反应」→ **静默跑旧代码**。
REM   那比不reload 更危险，所以宁可不要它。
REM
REM 代价（已知并接受）：uvicorn 默认递归监视整个工作目录的 *.py，**含 tests/**。
REM   所以「跑真实任务时新增/改测试文件」会重启服务 → 在跑的会话被杀 →
REM   recovery 用同一 session_id 重跑 → 状态重置 + 重新烧一遍 agnes 额度。
REM   → 跑真实任务期间避免动 tests/ 下的文件。
REM
REM 日志：应用自身写 agent-service\data\logs\agent.log（10MB × 5 份轮转）。
REM       改完 agent 代码后可 tail 该文件确认真的重载了（会打印新的
REM       「文件日志已启用」+「VideoPoller 启动」）。
REM ============================================================================
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
