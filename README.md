# KGExtraction 知识图谱抽取系统

一个基于大语言模型的**可治理知识工程工作台**，支持从 PDF、Word、Markdown、TXT、Excel 等文档中抽取结构化知识，构建可溯源、可校验、可演化的知识图谱，并提供多模式溯源式问答（Graph RAG）。

> 📖 **新用户请先读 [使用指南](docs/使用指南.md)** —— 产品介绍、核心概念、完整操作流程与常见问题。

## ✨ 核心特性

- **统一工作台**：左侧导航四工作区「数据接入 → 本体与计划 → 抽取与治理 → 图谱与问图」，导航项带实时状态徽标（解析中/运行中/待复核/门控违规）
- **知识抽象度分道**：surface/normalized/inductive 三档，表面实体与归纳知识（概念/规则）各走不同的抽取、证据与可信度流程，全端蓝/青/紫三色贯穿
- **本体编译抽取流程**：Schema 编译出可审查的抽取计划（Plan），按抽象度分道的泳道视图常驻展示，每步可查看编排理由
- **稳定实体身份**：实体身份 = (名称, 类型)，节点/边 ID 确定性派生，跨抽取任务与版本稳定，支撑增量演化与外部引用
- **可验证证据锚定**：表面知识逐字校验命中原文；归纳知识以支撑案例数为客观可信度依据，跨案例语义归并累加
- **三层图谱**：知识层（Schema 实体关系）+ 结构层（文档→章节→片段树）+ 桥接层（提及于/共现边），概念的跨文档分布可遍历，孤立节点挂靠语料
- **确定性发布门控**：LLM 提案、确定性引擎裁决；Schema 外类型不静默丢弃而落为被拒项，仅合规知识进入发布图
- **增量复核队列**：草稿相对已发布图的增量 diff，按风险排序、色条分诊，支持就地改类型；全程审计留痕（时间线呈现）
- **成本可预估可控**：启动前展示确定性成本预估（含归纳分道额外开销），支持单任务 token 预算上限、超限优雅停止
- **多模式 Graph RAG**：默认智能路由（文本检索/子图扩展/HippoRAG 式二部图 PPR/社区摘要），回答带 [来源#n] 引用锚溯源到原文，命中子图在图上点亮
- **最小访问控制**：可选 KG_ACCESS_TOKEN 令牌鉴权 + 操作人标识写入审计日志，适配团队部署
- **私有化友好**：文件系统 + SQLite + 本地向量库（FAISS），无强依赖外部图数据库

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
├── frontend/                 # 前端工作台（暗色主题 + 左侧导航四工作区）
│   └── src/
│       ├── api/             # API 接口封装
│       ├── store/           # 全局项目上下文 + 状态徽标轮询
│       ├── pages/           # 页面组件
│       │   ├── workspaces.tsx      # 数据接入 / 本体与计划 / 抽取与治理
│       │   └── GraphPage.tsx       # 图谱与问图
│       └── components/      # AppRail(左导航) / SchemaEditor / PlanLanes / ExtractionRunner / ReviewQueue ...
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
