# KGExtraction 部署操作手册 (本地与远程分离版)

本手册详细区分了在**本地开发机**和**远程服务器**上执行的步骤。所有依赖安装均已配置为**阿里云国内镜像源**以加速构建。

---

## 一、 本地执行任务 (Local)

在您自己的电脑上完成以下操作，为部署做准备。

### 1. 修改配置文件
- **`.env` 文件**：
  复制 `.env.example` 为 `.env`，并填入您的 API Key (如 OpenAI)。
  ```bash
  cp .env.example .env
  ```
- **`docker-compose.yml`**：
  确认端口已设置为 `8081:80`。

### 2. 将代码推送至服务器 (推荐方式)
**不要**直接使用 `scp -r`，因为这会把巨大的 `node_modules` 也传上去，非常浪费时间。

推荐使用 `rsync` 命令，它可以排查无关文件夹：
```bash
# 在本地项目父目录下执行 (确保排除 node_modules 和 .git)
rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.DS_Store' ./KGExtraction root@47.102.111.107:/root/
```

> [!NOTE]
> `node_modules` 无需拷贝，服务器上的 Docker 构建过程会自动根据镜像源重新安装。

---

## 二、 远程服务器执行任务 (Remote)

在您已经 SSH 登录的远程服务器 (`root@47.102.111.107`) 上执行以下操作。

### 1. 环境验证
确保服务器已安装 Docker 和 Docker Compose。
```bash
docker --version
docker-compose --version
```

### 2. 进入项目目录
```bash
cd /root/KGExtraction
```

### 3. 构建与部署 (加速版)
执行以下命令启动项目。我们在 Dockerfile 中预置了阿里云镜像加速：
- **Backend (Python)**: 使用 `mirrors.aliyun.com` 加速 `apt` 和 `pip`。
- **Frontend (Node/Nginx)**: 使用 `registry.npmmirror.com` 加速 `npm`。

```bash
# 构建并后台启动
docker-compose up -d --build
```

### 4. 确认运行状态
```bash
docker-compose ps
```

---

## 三、 访问与安全配置

### 1. 端口开放 (重要)
登录您的云服务商 (如阿里云) 控制台，找到 **安全组 (Security Group)** 设置：
- **添加规则**：允许 **TCP 8081** 端口入站。

### 2. 访问应用
浏览器访问：`http://47.102.111.107:8081`

---

## 四、 常用维护命令 (Remote)

- **查看实时日志**：
  ```bash
  docker-compose logs -f
  ```
- **清理已停止的容器和镜像**：
  ```bash
  docker system prune -a
  ```
- **停止服务**：
  ```bash
  docker-compose down
  ```

---
*KGExtraction 知识图谱抽取系统*
