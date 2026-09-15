#!/usr/bin/env bash
# DreamWeaver agent-service 开发启动（bash 版，等价于 run-dev.bat）
#
# ⚠️ --reload-dir app 是必须的，不要去掉：
#    uvicorn 默认递归监视**整个工作目录**的 *.py（含 tests/）。于是「跑真实任务时改
#    任何 .py、或跑测试新增 .py」都会重启服务 → 在跑的会话被杀 → recovery 用同一
#    session_id 重跑 → 状态重置 + 重新烧一遍 agnes 额度。（2026-09-15 实测踩到）
#
# 日志：agent-service/data/logs/agent.log（10MB × 5 份轮转，应用自身写入）
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/Scripts/python.exe -m uvicorn app.main:app \
    --reload --reload-dir app --port 8000 "$@"
