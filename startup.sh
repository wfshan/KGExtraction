#!/bin/bash
# ================================================
# KGExtraction 一键启动脚本
# ================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "================================================"
echo "  🚀 KGExtraction 知识图谱抽取系统"
echo "================================================"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "❌ 未找到 node，请先安装 Node.js 18+"
    exit 1
fi

# 安装后端依赖
echo ""
echo "📦 安装后端依赖..."
cd "$BACKEND_DIR"
set +e
pip3 install -r requirements.txt
INSTALL_EXIT_CODE=$?
if [ $INSTALL_EXIT_CODE -ne 0 ]; then
    echo "⚠️  默认 pip 源安装失败，尝试使用官方 PyPI 源..."
    pip3 install -r requirements.txt --index-url https://pypi.org/simple
    INSTALL_EXIT_CODE=$?
fi
if [ $INSTALL_EXIT_CODE -ne 0 ]; then
    echo "⚠️  官方 PyPI 安装失败，尝试使用清华镜像..."
    pip3 install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
    INSTALL_EXIT_CODE=$?
fi
set -e
if [ $INSTALL_EXIT_CODE -ne 0 ]; then
    echo "❌ 后端依赖安装失败，请检查网络或 pip 配置后重试"
    exit 1
fi

# 安装前端依赖
echo ""
echo "📦 安装前端依赖..."
cd "$FRONTEND_DIR"
npm install --silent

# 创建 .env 文件（如果不存在）
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "⚠️  已创建 .env 文件，请编辑填入你的 API Key"
fi

# 清理可能被占用的端口
echo ""
echo "🧹 清理可能被占用的端口 (8000, 5173)..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :5173 | xargs kill -9 2>/dev/null || true

# 启动后端
echo ""
echo "🔧 启动后端服务 (FastAPI)..."
cd "$BACKEND_DIR"
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 等待后端就绪
sleep 2

# 启动前端
echo "🎨 启动前端服务 (Vite)..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "================================================"
echo "  ✅ 服务已启动"
echo "  📊 前端: http://localhost:5173"
echo "  🔌 后端: http://localhost:8000"
echo "  📖 API 文档: http://localhost:8000/docs"
echo "================================================"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 捕获退出信号
cleanup() {
    echo ""
    echo "🛑 正在停止服务..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo "✅ 服务已停止"
    exit 0
}
trap cleanup INT TERM

# 等待
wait
