#!/bin/bash
# TravelAgent 一键停止：双击本文件即可
pids=$(lsof -ti :8000 2>/dev/null)
if [ -n "$pids" ]; then kill $pids; echo "🛑 后端已停止"; else echo "ℹ️ 后端未在运行"; fi

pids=$(lsof -ti :5173 2>/dev/null)
if [ -n "$pids" ]; then kill $pids; echo "🛑 前端已停止"; else echo "ℹ️ 前端未在运行"; fi

echo ""
read -p "按回车关闭本窗口..."
