# ==================== 前端 Dockerfile ====================
# 多阶段构建: builder (npm build) + runner (nginx)
FROM node:20-alpine AS builder

WORKDIR /app

# 安装依赖
COPY frontend-v2/package.json ./
RUN npm install

# 构建
COPY frontend-v2/ .
RUN npm run build

# ========================= 运行阶段 =========================
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
