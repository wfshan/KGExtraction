# KGExtraction 知识图谱抽取系统

一个基于大语言模型的轻量级知识图谱抽取工具，支持从 PDF、Word、Markdown、TXT 等文档中自动抽取结构化知识，构建可视化知识图谱。

## ✨ 核心特性

- **流程化工作台**：分步向导引导完成「项目管理 → 文档接入(含冷启动导入) → Schema 配置 → 智能抽取 → 人工复核」
- **稳定实体身份**：实体身份 = (名称, 类型)，节点/边 ID 确定性派生，跨抽取任务与版本稳定，支撑增量演化与外部引用
- **可验证证据锚定**：抽取时要求 LLM 返回原文支撑短句，并**逐字校验命中原文**（verified 标记）；发布门控可拦截幻觉证据
- **可审计的严格校验**：Schema 外的实体/关系不静默丢弃，落库为被拒项供人工复查，同时作为 Schema 缺口检测的信号源
- **增量复核队列**：复核对象是草稿相对已发布图的**增量 diff**，按风险排序（门控违规 > 未验证证据 > 低置信度），逐项裁决全程审计留痕
- **成本可预估可控**：启动抽取前展示确定性成本预估（调用次数/token/费用），支持单任务 token 预算上限，超限优雅停止
- **智能抽取引擎**：分片并发流水线，多阶段自我修正、实时实体消歧（(名称,类型) 复合键 + 向量召回 + LLM 裁决）
- **智能模型路由**：根据任务复杂度（轻量/均衡/强力）自动路由模型，平衡效果与成本
- **层分离图谱**：文档结构层（片段锚点/「下一段」边）与知识层分离，图算法（子图/PPR/社区检测）只在知识层运行
- **图谱可视化与交互**：Cytoscape.js 力导向图增量渲染（避免抖动），支持框选子图查询、分组着色、缩放详情查看
- **多模式 Graph RAG**：默认**智能路由**（按查询自动选择 文本检索/子图扩展/PPR 关联/社区摘要），支持历史会话上下文
- **最小访问控制**：可选 KG_ACCESS_TOKEN 令牌鉴权 + 操作人标识写入审计日志，适配团队部署
- **轻便透明的数据流**：依赖文件系统实现 JSON 快照、Local Vector DB 检索，零缝隙适配私有化部署场景

## 🏗️ 技术栈

| 层次       | 技术                                            |
| ---------- | ----------------------------------------------- |
| 前端       | React + TypeScript + Vite + Ant Design          |
| 图谱可视化 | Cytoscape.js                                    |
| 后端       | Python FastAPI                                  |
| 抽取编排   | 自研 asyncio 并发流水线（Semaphore 限流 + 批量落库） |
| LLM 接口   | OpenAI SDK 兼容模式（默认接入阿里云 DashScope） |
| 文档解析   | pypdf + python-docx + markdown-it-py            |
| 向量检索   | FAISS + sentence-transformers (bge-small-zh)    |
| Graph RAG  | NetworkX (内存子图提取) + LLM 文本路由合成      |
| 数据存储   | 项目级 JSON + 节点级文档文本关联索引映射          |

## 🚀 快速开始

### 环境要求

- Python 3.9+
- Node.js 18+
- npm

### 安装与启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key

# 2. 一键启动
chmod +x startup.sh
bash startup.sh
```

启动后：

- 前端工作台：<http://localhost:5173>
- 后端 API：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

```bash
# 后端
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端（新终端）
cd frontend
npm install
npm run dev
```

## 🐳 远程部署 (Docker)

推荐使用 Docker 进行生产环境部署，以确保环境一致性。

### 1. 前置要求
- 安装 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)。

### 2. 部署步骤
```bash
# 1. 下载源码并进入目录
git clone <your-repo-url>
cd KGExtraction

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 和其他配置

# 3. 启动容器
docker-compose up --build -d
```

### 3. 访问
- **前端页面**: `http://你的服务器IP` (默认 80 端口)
- **后端 API**: `http://你的服务器IP:8000`

### 4. 维护
- 查看日志: `docker-compose logs -f`
- 停止服务: `docker-compose down`
- 更新代码后重新构建: `docker-compose up --build -d`

## 📁 项目结构

```
KGExtraction/
├── backend/                  # 后端服务
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 系统配置管理
│   ├── models/              # 数据模型
│   │   ├── project.py       # 项目模型
│   │   ├── document.py      # 文档模型
│   │   ├── schema.py        # Schema 模型
│   │   ├── graph.py         # 图谱模型
│   │   └── run.py           # 运行任务模型
│   ├── routers/             # API 路由
│   │   ├── system.py        # 系统配置
│   │   ├── projects.py      # 项目管理
│   │   ├── documents.py     # 文档管理 (含冷启动导入图谱)
│   │   ├── schema.py        # Schema 配置
│   │   ├── runs.py          # 抽取任务
│   │   ├── graph.py         # 图谱数据CRUD
│   │   └── graph_rag.py     # Graph RAG 图谱问答
│   ├── services/            # 核心服务
│   │   ├── parser.py        # 文档解析器
│   │   ├── chunker.py       # 文本分片
│   │   ├── llm_gateway.py   # LLM 动态路由模型网关
│   │   ├── embedding.py     # 向量特征化
│   │   ├── vector_store.py  # FAISS 索引
│   │   ├── graph_store.py   # JSON / NetworkX 图管理
│   │   ├── chat_store.py    # 问图功能对话记录管理
│   │   ├── chunk_store.py   # 原文片段映射溯源
│   │   ├── schema_suggestion.py
│   │   ├── extraction_logger.py # 抽取记录分级日志
│   │   └── extraction/      # 抽取引流
│   │       ├── graph.py     # 状态机编排
│   │       ├── entity.py    # 实体识别对齐
│   │       ├── relation.py  # 跨分片链接推断
│   │       ├── correction.py # 反射修正
│   │       └── prompts.py   # 结构化抽取模板
│   └── requirements.txt
├── frontend/                 # 前端工作台
│   └── src/
│       ├── api/             # API 接口封装
│       ├── pages/           # 页面组件
│       │   ├── WorkbenchPage.tsx  # 工作台
│       │   └── GraphPage.tsx     # 图谱可视化
│       └── components/      # UI 组件
├── .env.example             # 环境变量模板
├── startup.sh               # 一键启动脚本
└── README.md
```

## 🔧 配置说明

### 环境变量 (.env)

| 变量名            | 说明                | 默认值                                            |
| ----------------- | ------------------- | ------------------------------------------------- |
| DASHSCOPE_API_KEY | 阿里云百炼 API Key  | -                                                 |
| BASE_URL          | 大模型 API Base URL | <https://dashscope.aliyuncs.com/compatible-mode/v1> |
| KG_ACCESS_TOKEN   | 访问令牌（可选）。配置后所有 API 请求须携带 `Authorization: Bearer <令牌>`；前端在「系统配置 → 身份与访问」填写。**网络/团队部署时必须配置** | 空（不启用鉴权） |

### 审计与操作人

前端「系统配置 → 身份与访问」中填写**操作人名称**后，发布/驳回/增删改/复核裁决
均以该身份写入项目审计日志（人工复核页「审计日志」标签可查）。

### 系统内置配置

通过工作台右上角 ⚙️ 按钮可调整：

- 模型选择（轻量/均衡/强力）
- 分片参数（大小、重叠）
- 向量检索参数（Top-K、阈值）

## 📄 许可证

MIT License
