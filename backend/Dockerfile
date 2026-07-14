# Backend Dockerfile for KGExtraction
FROM docker.m.daocloud.io/library/python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统开发工具
RUN sed -i "s/deb.debian.org/mirrors.aliyun.com/g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 优化：显式安装 CPU 版的 Torch 以节省巨大磁盘空间 (约 2GB)。
# torch 从阿里云 pytorch-wheels 镜像直装（pytorch 官方源在国内服务器上
# 经常 stall 数十分钟）；固定 wheel 与基础镜像 python:3.11-slim/x86_64 对应，
# 升级基础镜像时需同步更新此 URL。
RUN pip install --no-cache-dir --default-timeout=1000 -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
        "torch @ https://mirrors.aliyun.com/pytorch-wheels/cpu/torch-2.13.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl" && \
    pip install --no-cache-dir --default-timeout=1000 -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 复制源码
COPY . .

# 创建必要的目录
RUN mkdir -p data/uploads data/indices

# 暴露端口
EXPOSE 8000

# 启动服务
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
