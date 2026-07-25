# ==================== 后端 Dockerfile ====================
# 多阶段构建: builder (安装依赖) + runner (运行)
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ========================= 运行阶段 =========================
FROM python:3.11-slim

WORKDIR /app

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制已安装的 Python 包
COPY --from=builder /root/.local /root/.local

# 复制应用代码
COPY backend/ ./backend/

# 设置 PATH
ENV PATH=/root/.local/bin:$PATH

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
