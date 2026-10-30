# ==================== 后端镜像（V5）====================
# 变更：
#   - 移除 Node.js（不再调用 westock-tool CLI）
#   - 统一 TZ=Asia/Shanghai（V4 容器默认 UTC，15:05 实际是北京时间 23:05）
#   - **安装 Playwright Chromium**（新闻链路 Scrapy+Playwright 需要浏览器内核；
#     只装 Python 包会在真正爬取时报 "Executable doesn't exist"）
#   - 入口脚本：bootstrap_db → 空库灌演示数据 → uvicorn
#   - HEALTHCHECK 走 /health（含数据库连通性）
# 构建上下文：./backend
# 锁定 bookworm（Debian 12）：python:3.11-slim 已漂移到 trixie（Debian 13），
#   Playwright 1.42 不识别 trixie，--with-deps 会按 ubuntu20.04 清单装
#   ttf-unifont / ttf-ubuntu-font-family 等不存在的包，apt exit 100。
# apt 源切阿里云：deb.debian.org 国内实测 ~24kB/s（10MB 跑 7 分钟）。
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/


# ========================= 运行阶段 =========================
FROM python:3.11-slim-bookworm

WORKDIR /app

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 先用 builder 装好的 Python 包（含 playwright），再装 Chromium 及其系统依赖
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# --with-deps 会安装 chromium 运行所需的 libnss3/libatk/libgbm 等系统库
# PLAYWRIGHT_DOWNLOAD_HOST：Chromium 二进制走 npmmirror 国内源，官方 CDN 国内易超时
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && python -m playwright --version

COPY . .

# 可执行权限（Windows 检出的文件常丢 +x）
RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/bin/sh", "docker/entrypoint.sh"]
