#!/bin/bash
# TravelAgent 一键启动：双击本文件即可（后端 :8000 + 前端 :5173）
cd "$(dirname "$0")"

# ---- 后端 ----
if lsof -ti :8000 >/dev/null 2>&1; then
  echo "✅ 后端已在运行，跳过"
else
  (cd backend && nohup .venv/bin/uvicorn app.main:app --port 8000 --reload > /tmp/ta-backend.log 2>&1 &)
  disown
  echo "⏳ 后端启动中...（日志: /tmp/ta-backend.log）"
fi

# ---- 前端 ----
if lsof -ti :5173 >/dev/null 2>&1; then
  echo "✅ 前端已在运行，跳过"
else
  (cd frontend && nohup npm run dev > /tmp/ta-frontend.log 2>&1 &)
  echo "⏳ 前端启动中...（日志: /tmp/ta-frontend.log）"
fi

# ---- 健康检查 ----
sleep 4
echo "----"
if curl -s -m 3 http://127.0.0.1:8000/api/health | grep -q ok; then
  echo "✅ 后端正常"
else
  echo "❌ 后端未响应，查看日志: tail -30 /tmp/ta-backend.log"
fi
echo ""
echo "👉 浏览器打开  http://localhost:5173"
echo "👉 停止服务请双击 stop.command"
echo ""
read -p "按回车关闭本窗口（服务继续在后台运行）..."
