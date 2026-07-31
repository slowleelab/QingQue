#!/bin/bash
# 前后端分离部署测试脚本
# 测试 CORS 配置和跨域请求

echo "=========================================="
echo "前后端分离部署测试"
echo "=========================================="
echo ""

# 后端地址
BACKEND="http://localhost:8081"

echo "=== 1. 测试 CORS 预检请求 (OPTIONS) ==="
echo "请求: OPTIONS $BACKEND/api/agent/test"
echo "Origin: http://localhost:3000 (模拟前端地址)"
echo ""
curl -s -I -X OPTIONS "$BACKEND/api/agent/test" \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Content-Type" | grep -E "HTTP|Access-Control"
echo ""

echo "=== 2. 测试 CORS 实际请求 ==="
echo "请求: GET $BACKEND/api/agent/agent-001"
echo "Origin: http://localhost:3000 (模拟前端地址)"
echo ""
curl -s -D - "$BACKEND/api/agent/agent-001" \
  -H "Origin: http://localhost:3000" 2>&1 | head -10
echo ""

echo "=== 3. 测试 WebSocket 配置 ==="
echo "检查 agent.js 配置:"
curl -s "$BACKEND/js/agent.js" | grep -A 10 "const CONFIG" | head -8
echo ""

echo "=== 4. 测试 config.js 可访问性 ==="
echo "请求: GET $BACKEND/js/config.js"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" "$BACKEND/js/config.js"
echo ""

echo "=== 5. 测试不同 Origin 的 CORS 请求 ==="
echo ""

# 测试允许的 Origin
for origin in "http://localhost:3000" "http://localhost:8081" "https://example.com"; do
  echo "测试 Origin: $origin"
  response=$(curl -s -I -X OPTIONS "$BACKEND/api/agent/test" \
    -H "Origin: $origin" \
    -H "Access-Control-Request-Method: GET" 2>&1)

  allow_origin=$(echo "$response" | grep -i "Access-Control-Allow-Origin" | head -1)
  if [ -n "$allow_origin" ]; then
    echo "  ✓ CORS 允许: $allow_origin"
  else
    echo "  ✗ CORS 拒绝"
  fi
  echo ""
done

echo "=========================================="
echo "测试完成"
echo "=========================================="
