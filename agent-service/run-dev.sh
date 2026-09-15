#!/usr/bin/env bash
# DreamWeaver agent-service 开发启动（bash 版，等价于 run-dev.bat）
#
# ⚠️ 用**纯 --reload**，不要加 --reload-dir！
#
# 2026-09-15 实测（隔离实例对照，端口 8001/8004）：
#   ✓ 纯 --reload（监视整个 agent-service）  → 改 app/xxx.py 内容**会**重载
#   ✗ --reload --reload-dir app               → 改 app/xxx.py 内容**不会**重载
#                                               （只有新增/删除文件才触发）
#
#   即 --reload-dir 在本机（Windows + watchfiles 1.2.0）对「已有文件的内容变更」失效，
#   表现为「启动日志说在监视、改代码却毫无反应」→ **静默跑旧代码**。
#   那比不 reload 更危险，所以宁可不要它。
#
# 代价（已知并接受）：uvicorn 默认递归监视整个工作目录的 *.py，含 tests/。
#   所以「跑真实任务时新增/改测试文件」会重启服务 → 在跑的会话被杀 →
#   recovery 用同一 session_id 重跑 → 状态重置 + 重新烧一遍 agnes 额度。
#   → 跑真实任务期间避免动 tests/ 下的文件。
#
# 日志：agent-service/data/logs/agent.log（改完代码后 tail 它确认真的重载了）
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000 "$@"
