#!/usr/bin/env bash
# 启动开发环境

set -e

echo "🚀 Starting EAS 2.0 Development Environment"

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found"
    exit 1
fi

# 检查Rust环境
if ! command -v cargo &> /dev/null; then
    echo "❌ Cargo not found"
    exit 1
fi

# 创建数据目录
mkdir -p data logs

# 启动Python API服务
echo "📦 Starting Python API service..."
cd python
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 9000 --reload &
PYTHON_PID=$!
cd ..

# 等待Python服务启动
sleep 3

# 启动Rust服务器
echo "🦀 Starting Rust server..."
cargo run --bin eas-server &
RUST_PID=$!

echo "✅ Services started:"
echo "   - Python API: http://localhost:9000"
echo "   - Rust Server: http://localhost:8090"
echo ""
echo "Press Ctrl+C to stop all services"

# 等待中断信号
trap "kill $PYTHON_PID $RUST_PID 2>/dev/null; exit" INT TERM

wait
