# ==================== 前端镜像（V5）====================
# 变更：
#   - 构建上下文改为 ./frontend（原 context: . 会把 Windows 版 node_modules 覆盖进 Linux 构建层）
#   - 用 package-lock.json + npm ci 保证可复现（lock 缺失时回退 npm install）
# 构建上下文：./frontend
FROM node:20-alpine AS builder

WORKDIR /app

# 先只拷依赖清单，最大化利用层缓存
COPY package.json package-lock.json* ./
# package.json 与 lock 可能不同步（例如删过依赖），npm ci 会硬失败 → 回退 npm install
RUN if [ -f package-lock.json ]; then npm ci || npm install; else npm install; fi

COPY . .
RUN npm run build


# ========================= 运行阶段 =========================
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
