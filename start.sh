#!/usr/bin/env bash
# ============================================================
# GFNA V5 一键启动（本地开发）
#   1. 检查 Python 3.11+
#   2. 创建/激活 backend/.venv
#   3. 安装依赖（可用 PIP_INDEX_URL 指定镜像）
#   4. 检查 .env
#   5. 初始化数据库（建库/建表/分区/视图/种子）
#   6. 启动 FastAPI
# 用法：
#   chmod +x start.sh && ./start.sh
#   PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ ./start.sh
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}   GFNA V5 · A 股行情与量化选股 · 启动脚本${NC}"
echo -e "${BLUE}============================================================${NC}"

# ---------- 1. Python ----------
echo -e "${YELLOW}[1/6] 检查 Python ...${NC}"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo -e "${RED}未找到 Python，请安装 Python 3.11+${NC}"; exit 1
fi
echo -e "${GREEN}  $($PYTHON --version)${NC}"

# ---------- 2. venv ----------
echo -e "${YELLOW}[2/6] 虚拟环境 ...${NC}"
VENV_DIR="$SCRIPT_DIR/backend/.venv"
[ -d "$VENV_DIR" ] || "$PYTHON" -m venv "$VENV_DIR"
if [ -f "$VENV_DIR/bin/activate" ]; then source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then source "$VENV_DIR/Scripts/activate"; fi
echo -e "${GREEN}  已激活 $VENV_DIR${NC}"

# ---------- 3. 依赖 ----------
echo -e "${YELLOW}[3/6] 安装依赖 ...${NC}"
PIP_ARGS=(-q)
[ -n "${PIP_INDEX_URL:-}" ] && PIP_ARGS+=(-i "$PIP_INDEX_URL")
python -m pip install --upgrade pip "${PIP_ARGS[@]}"
python -m pip install -r "$SCRIPT_DIR/backend/requirements.txt" "${PIP_ARGS[@]}"
echo -e "${GREEN}  依赖就绪${NC}"

# ---------- 4. .env ----------
echo -e "${YELLOW}[4/6] 检查配置 ...${NC}"
if [ ! -f "$SCRIPT_DIR/backend/.env" ]; then
  cp "$SCRIPT_DIR/backend/.env.example" "$SCRIPT_DIR/backend/.env"
  echo -e "${YELLOW}  已从 .env.example 生成 backend/.env，请填写 POSTGRES_PASSWORD 与 DEEPSEEK_API_KEY${NC}"
else
  echo -e "${GREEN}  backend/.env 已存在${NC}"
fi

# ---------- 5. 数据库 ----------
echo -e "${YELLOW}[5/6] 初始化数据库（幂等）...${NC}"
pushd "$SCRIPT_DIR/backend" >/dev/null
python scripts/bootstrap_db.py
popd >/dev/null
echo -e "${GREEN}  数据库就绪（如需重新采集：python scripts/pipeline_cli.py all）${NC}"

# ---------- 6. 启动 ----------
echo -e "${YELLOW}[6/6] 启动 FastAPI ...${NC}"
echo -e "  API 文档:  ${GREEN}http://localhost:${SERVER_PORT:-8000}/docs${NC}"
echo -e "  健康检查:  ${GREEN}http://localhost:${SERVER_PORT:-8000}/health${NC}"
echo -e "  选股中心:  ${GREEN}http://localhost:5173/algo-center${NC}（需另行 npm run dev）"
echo -e "${YELLOW}  Ctrl+C 停止${NC}"

cd "$SCRIPT_DIR/backend"
export PYTHONPATH="$SCRIPT_DIR/backend:${PYTHONPATH:-}"
exec python -m uvicorn app.main:app \
  --host "${SERVER_HOST:-127.0.0.1}" \
  --port "${SERVER_PORT:-8000}" \
  --reload \
  --log-level "$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"
