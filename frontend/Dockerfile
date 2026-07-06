# Frontend Dockerfile for KGExtraction
# 第 1 阶段：构建环境
FROM docker.m.daocloud.io/library/node:18-slim AS builder

WORKDIR /app

# 复制 package.json 和 lock 文件
COPY package*.json ./

# 安装依赖
# 使用国内镜像加速
RUN npm install --registry=https://registry.npmmirror.com

# 复制源码
COPY . .

# 执行构建（默认输出到 dist 目录）
# 生产环境下通常会设置 VITE_API_URL 为 /api，让 Nginx 代理
ENV VITE_API_URL=/api
RUN npm run build

# 第 2 阶段：运行环境 (Nginx)
FROM docker.m.daocloud.io/library/nginx:stable-alpine

# 复制构建产物到 Nginx 目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制自定义 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 暴露端口
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
