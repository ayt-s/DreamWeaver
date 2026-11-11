#!/usr/bin/env bash
# 鉴权冒烟测试（真实 HTTP，不需要 Apifox / 不需要单测框架）。
#
# 用法：
#   bash scripts/smoke-auth.sh                      # 默认 http://127.0.0.1:8080
#   BASE=http://127.0.0.1:8080 bash scripts/smoke-auth.sh
#
# 为什么保留这个脚本：Apifox 场景适合协作与展示，但它依赖平台的服务地址解析；
# 这份脚本只依赖 curl，能在本机/CI 里直接判定「鉴权有没有真的生效」。
#
# 退出码：0 全过；1 有失败（可直接接 CI）。

set -u

BASE="${BASE:-http://127.0.0.1:8080}"
USER="smoke_$(date +%s)"
PASS="smoke12345"
pass=0
fail=0

# curl 同时拿 body 和状态码：避免用临时文件（原生 curl 在 Git Bash 下写 /tmp 会失败）
req() {
  local resp
  resp=$(curl -s -w $'\n%{http_code}' "$@")
  CODE="${resp##*$'\n'}"
  BODY="${resp%$'\n'*}"
}

check() { # 名称 期望 实得
  if [ "$2" = "$3" ]; then
    pass=$((pass + 1)); printf 'PASS  %s  (%s)\n' "$1" "$3"
  else
    fail=$((fail + 1)); printf 'FAIL  %s  期望 %s 实得 %s  body=%s\n' "$1" "$2" "$3" "$(printf '%s' "$BODY" | head -c 200)"
  fi
}

echo "== 0. 等服务就绪（最多 120s）=="
for _ in $(seq 1 24); do
  req "$BASE/actuator/health"
  [ "$CODE" = "200" ] && { echo "服务就绪"; break; }
  sleep 5
done

echo "== 1. 未登录访问受保护接口 =="
req "$BASE/api/tasks"
check "无 token 访问 /api/tasks 应 401" 401 "$CODE"

echo "== 2. 只伪造 X-User-Id（阶段 1 的漏洞）=="
req -H "X-User-Id: 999" "$BASE/api/tasks"
check "仅伪造 X-User-Id 应 401" 401 "$CODE"

echo "== 3. 注册 =="
req -X POST "$BASE/api/auth/register" -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}"
check "注册应 200" 200 "$CODE"
TOKEN=$(printf '%s' "$BODY" | grep -oE '"token":"[^"]+"' | head -1 | cut -d'"' -f4)
if [ -n "${TOKEN:-}" ]; then
  pass=$((pass + 1)); printf 'PASS  注册返回 token（长度 %s）\n' "${#TOKEN}"
else
  fail=$((fail + 1)); echo "FAIL  注册未返回 token"
fi

echo "== 4. 重复注册同名 =="
req -X POST "$BASE/api/auth/register" -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}"
check "重复用户名应 400" 400 "$CODE"

echo "== 5. 错误密码登录 =="
req -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"wrong-password\"}"
check "错误密码应 401" 401 "$CODE"
case "$BODY" in
  *"用户名或密码错误"*) pass=$((pass + 1)); echo "PASS  文案统一（不暴露用户是否存在）" ;;
  *) fail=$((fail + 1)); echo "FAIL  文案不符: $(printf '%s' "$BODY" | head -c 120)" ;;
esac

echo "== 6. 正确登录 =="
req -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}"
check "正确登录应 200" 200 "$CODE"

echo "== 7. /api/auth/me（身份必须来自 token）=="
req -H "Authorization: Bearer ${TOKEN:-}" "$BASE/api/auth/me"
check "me 应 200" 200 "$CODE"
case "$BODY" in
  *"\"username\":\"$USER\""*) pass=$((pass + 1)); echo "PASS  me 返回 token 里的用户" ;;
  *) fail=$((fail + 1)); echo "FAIL  me 内容不符: $(printf '%s' "$BODY" | head -c 160)" ;;
esac

echo "== 8. 带 token 访问业务接口 =="
req -H "Authorization: Bearer ${TOKEN:-}" "$BASE/api/tasks"
check "带 token 访问 /api/tasks 应 200" 200 "$CODE"

echo "== 9. 伪造 token =="
req -H "Authorization: Bearer not-a-jwt" "$BASE/api/tasks"
check "伪造 token 应 401" 401 "$CODE"

echo
echo "结果：PASS=$pass FAIL=$fail  测试账号=$USER"
[ "$fail" = "0" ] || exit 1
