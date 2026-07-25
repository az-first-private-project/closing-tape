#!/bin/bash
# ============================================================
# GFNA (全球财经新闻聚合器) 一键启动脚本
# ============================================================
# 功能:
#   1. 检查 Python 和环境依赖
#   2. 创建虚拟环境 (可选)
#   3. 安装 Python 依赖
#   4. 检查并初始化 MySQL 数据库
#   5. 安装 Playwright 浏览器
#   6. 启动 FastAPI 后端服务
# ============================================================
# 使用方法:
#   chmod +x start.sh
#   ./start.sh
# ============================================================

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # 无色

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}   GFNA - 全球财经新闻聚合器 启动脚本${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ==================== 1. 检查 Python ====================
echo -e "${YELLOW}[1/6] 检查 Python 环境...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "${RED}错误: 未找到 Python，请先安装 Python 3.9+${NC}"
    echo "下载: https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}  找到 Python: ${PYTHON_VERSION}${NC}"
echo ""

# ==================== 2. 虚拟环境 ====================
echo -e "${YELLOW}[2/6] 设置虚拟环境...${NC}"

# 检查虚拟环境目录
VENV_DIR="$SCRIPT_DIR/backend/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "  创建虚拟环境: $VENV_DIR"
    $PYTHON -m venv "$VENV_DIR"
fi

# 激活虚拟环境
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
fi

echo -e "${GREEN}  虚拟环境已激活${NC}"
echo ""

# ==================== 3. 安装依赖 ====================
echo -e "${YELLOW}[3/6] 安装 Python 依赖...${NC}"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/backend/requirements.txt" -q
echo -e "${GREEN}  依赖安装完成${NC}"
echo ""

# ==================== 4. 配置 .env ====================
echo -e "${YELLOW}[4/6] 检查配置文件...${NC}"
if [ ! -f "$SCRIPT_DIR/backend/.env" ]; then
    echo -e "${YELLOW}  警告: 未找到 .env 文件${NC}"
    echo "  正在从 .env.example 复制..."
    cp "$SCRIPT_DIR/backend/.env.example" "$SCRIPT_DIR/backend/.env"
    echo -e "${YELLOW}  请编辑 backend/.env 填入你的配置:${NC}"
    echo -e "${YELLOW}    - MYSQL_PASSWORD: 你的 MySQL 密码${NC}"
    echo -e "${YELLOW}    - DEEPSEEK_API_KEY: 你的 DeepSeek API Key${NC}"
    echo ""
else
    echo -e "${GREEN}  .env 配置文件已存在${NC}"
fi
echo ""

# ==================== 5. 初始化数据库 ====================
echo -e "${YELLOW}[5/6] 初始化数据库...${NC}"

# 从 .env 读取数据库配置
if [ -f "$SCRIPT_DIR/backend/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/backend/.env" | xargs)
fi

if command -v mysql &> /dev/null; then
    echo "  执行 sql/init.sql ..."
    mysql -h"${MYSQL_HOST:-localhost}" \
          -P"${MYSQL_PORT:-3306}" \
          -u"${MYSQL_USER:-root}" \
          -p"${MYSQL_PASSWORD}" \
          < "$SCRIPT_DIR/sql/init.sql" 2>/dev/null || {
        echo -e "${YELLOW}  提示: 如果 MySQL 未运行，请先启动 MySQL 服务${NC}"
        echo -e "${YELLOW}  然后手动执行: mysql -u root -p < sql/init.sql${NC}"
    }
    echo -e "${GREEN}  数据库初始化完成${NC}"
else
    echo -e "${YELLOW}  未检测到 mysql 命令行工具${NC}"
    echo -e "${YELLOW}  请手动导入 SQL 脚本: mysql -u root -p < sql/init.sql${NC}"
fi
echo ""

# ==================== 6. 安装 Playwright 浏览器 ====================
echo -e "${YELLOW}[6/6] 检查 Playwright 浏览器...${NC}"
if python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    # 检查是否已安装 Chromium
    if ! python -c "
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        p.chromium.launch(headless=True)
    print('OK')
except:
    exit(1)
" 2>/dev/null; then
        echo "  安装 Playwright Chromium 浏览器..."
        python -m playwright install chromium
    else
        echo -e "${GREEN}  Playwright Chromium 已就绪${NC}"
    fi
else
    echo -e "${YELLOW}  Playwright 未安装，跳过${NC}"
    echo -e "${YELLOW}  运行: pip install playwright && python -m playwright install chromium${NC}"
fi
echo ""

# ==================== 启动服务 ====================
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  一切就绪! 正在启动 GFNA 服务...${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo -e "  API 文档: ${GREEN}http://localhost:${SERVER_PORT:-8000}/docs${NC}"
echo -e "  主页:     ${GREEN}http://localhost:${SERVER_PORT:-8000}/${NC}"
echo -e "  健康检查: ${GREEN}http://localhost:${SERVER_PORT:-8000}/health${NC}"
echo ""
echo -e "${YELLOW}  按 Ctrl+C 停止服务${NC}"
echo ""

# 切换到 backend 目录并启动 FastAPI
cd "$SCRIPT_DIR/backend"
export PYTHONPATH="$SCRIPT_DIR/backend:$PYTHONPATH"
python -m uvicorn app.main:app \
    --host "${SERVER_HOST:-0.0.0.0}" \
    --port "${SERVER_PORT:-8000}" \
    --reload \
    --log-level "${LOG_LEVEL:-info}"