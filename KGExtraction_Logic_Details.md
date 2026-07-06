# KGExtraction 技术架构与流程逻辑详述

本项目是一款基于大模型（LLM）的自动化知识图谱抽取与 RAG（检索增强生成）系统。本文档详细说明了系统每个流程、每个节点的实现逻辑、所用工艺（技术/工具）以及核心设计思想。

---

## 一、 系统全景架构

系统采用前后端分离架构，核心逻辑集中在后端服务层。

- **前端**: React + Vite + Ant Design + Cytoscape.js (可视化)
- **后端**: FastAPI + Pydantic v2 (异步 Web 框架)
- **大模型网关**: 自研 LLM Gateway，支持多模型按复杂度路由（Simple/Normal/Complex）
- **存储层**: 
  - **SQLite**: 存储非结构化存文本（Chunks）及结构化图数据（Nodes/Edges）
  - **FAISS**: 向量索引，支持实体消歧与语义检索
  - **文件系统**: 存储原始文档、解析缓存、日志及快照

---

## 二、 流程逻辑详述

系统工作流分为五个核心阶段。

### 1. 文档接入与预处理 (Document Pre-processing)

**目标**：将各种格式的非结构化文档转化为可供 LLM 处理的标准化分片。

| 节点名称 | 工艺/工具 | 逻辑描述 |
| :--- | :--- | :--- |
| **文档解析 (Parser)** | pypdf (PDF), python-docx (DOCX), markdown-it-py (MD) | 提取纯文本，针对不同格式进行清洗，保留标题层级结构。 |
| **文本分片 (Chunker)** | RecursiveCharacter / Hierarchical / Paragraph | **逻辑**：1. **递归切分**：优先在段落、句子边界处切分。2. **层级切分**：基于 Markdown/PDF 标题层级 (`#`, `第一章`, `1.1`) 进行结构化分割，并自动回溯上级标题作为分片上下文 (`Enrichment`)。设置 `chunk_overlap` 避免边界信息丢失。 |
| **存储入库** | SQLite (chunk_store.db) | 将分片内容与元数据（doc_id, index, offset）关联存储，支持按 ID 快速拉取原文。 |
| **向量化 (Indexing)** | FAISS + BGE-Small-Zh (Embedding) | 对分片进行向量化处理，建立 `vector.index`。**逻辑**：异步任务并行执行，为后续“纯文本检索模式”提供语义支持。 |

---

### 2. Schema 本体智能设计 (Ontological Design)

**目标**：在抽取前定义好“知识边界”，确保图谱的一致性。

| 节点名称 | 工艺/工具 | 逻辑描述 |
| :--- | :--- | :--- |
| **文档宏观分析 (Profiling)** | LLM (Complex 模型) | **逻辑**：均匀采样 10-15 个分片，让模型分析领域边界、核心概念及潜在关系语义。**输出**：`schema_profile.md`。 |
| **智能建议 (Suggestion)** | LLM (JSON Mode) | **逻辑**：基于分析报告，自动生成符合规范的实体类型（含颜色、定义）和关系类型（含 Source/Target 约束）。 |
| **自由对话优化** | WebSocket / Streaming Chat | **工艺**：采用流式对话，LLM 结合分析报告作为 Background，与用户讨论并动态调整 Schema。 |
| **Schema 生成** | Prompt Engineering | **逻辑**：将对话摘要输入模型，强制输出标准化 JSON。支持“定义自愈”：自动修正 Source/Target 与实体类型的匹配关系。 |

---

### 3. 智能抽取流水线 (Extraction Pipeline) - **核心**

**目标**：从文本中高准确度地识别三元组，并解决同名异义与跨片段推理问题。

| 节点名称 | 工艺/工具 | 逻辑描述 |
| :--- | :--- | :--- |
| **分片并发控制** | Asyncio.Semaphore | **工艺**：通过信号量限制并发数（默认 5），避免触发 LLM API 的 Rate Limit。 |
| **Schema 动态裁剪** | Python Set Logic | **逻辑**：如果用户为某个文档指定了特定的抽取目标，流水线会在 Prompt 生成前裁剪 Schema，降低模型干扰。 |
| **高精抽取 (One-pass/Multi-pass)** | LLM (Normal/Complex) | **One-pass**：一次调用输出 Entity+Relation，速度快。<br>**Multi-pass**：先抽实体，再基于实体列表抽关系，精度显著更高。 |
| **实体消歧 (Disambiguation)** | FAISS Vector Search + LLM Decision | **节点逻辑**：新实体名 -> 向量检索 -> 候选 ID 列表 -> LLM 判定 (`is_same`)。如判定为真，则合并 `source_chunk_ids` 而非新建节点。 |
| **跨片段关系推断 (Cross-inference)** | Global Entity Sampling + LLM | **逻辑**：提取当前片实体 + 采样全局活跃实体，询问 LLM 在本片段语境下它们是否有关。解决关系被物理分页切断的问题。 |
| **自我修正 (Correction)** | Rule-based + LLM Review | **工艺**：1. 规则过滤：类型不在 Schema 内的直接剔除。2. LLM 修正：检查关系是否违反了 Schema 的 Source/Target 类型约束。 |
| **文档结构固化** | Graph Topology (NetworkX) | **逻辑**：自动为分片建立 `文档片段` 节点并连线 `下一段`。这使得 GraphRAG 能根据图路径进行“跨段落阅读”。 |

---

### 4. 人工复核与发布 (Human-in-the-Loop)

**目标**：保证知识的准确性，支持“草稿-发布”两阶段变更。

- **草稿图 (Draft)**：抽取任务直接写入 `status='draft'`，前端提供 Cytoscape 可视化编辑器进行增删改。
- **发布逻辑**：
  - 复制记录到 `published` 状态。
  - 生成版本快照（Snapshot JSON）。
  - **关键动作**：将已发布的实体全量同步到 `entity.index`（向量库），供问答阶段检索。

---

### 5. Graph RAG 智能问答 (Question Answering)

**目标**：结合结构化推理能力与非结构化溯源，回答复杂问题。

| 节点名称 | 工艺/工具 | 逻辑描述 |
| :--- | :--- | :--- |
| **意图识别 (Intent)** | LLM (Simple) | 从问题中识别出 target_entities 和想要的 target_relation_types，缩小搜索半径。 |
| **种子实体链接 (Seed Linking)** | Semantic Match (FAISS) + Fast Match (String) | **工艺**：混合匹配。首选向量语义匹配，保底采用子串匹配。找到图谱中的“起点”。 |
| **子图扩展 (Subgraph Expansion)** | BFS (NetworkX) | **逻辑**：从起点开始扩散。`max_degree` 控制深度（通常为 1-2），`max_fanout` 控制广度。支持 `graph_flow`（有向流式检索）。 |
| **原文增强 (Context Augmentation)** | Chunker Store Lookup | **逻辑**：通过召回的边中的 `source_chunk_ids` 快速定位原始文本片段，作为“依据”通过 Prompt 喂给 LLM。 |
| **流式回答生成** | LLM (Normal) + Streaming | **Prompt 设计**：系统提示词强制要求“优先使用图谱三元组回答，并结合原文片段溯源”。 |

---

## 三、 参数选项与工艺配置 (Config.json)

系统通过 `system_config.json` 提供了丰富的调节杠杆：

1.  **Complexity Routing**:
    - `model_simple`: 用于意图识别、简单实体提取。
    - `model_normal`: 用于标准抽取、RAG 回答。
    - `model_complex`: 用于消歧、本体分析、自我修正。
2.  **Disambiguation Params**:
    - `fast_score_threshold`: 向量相似度超过此值直接合并，不走 LLM。
    - `score_threshold`: 向量搜索的最低召回阈值。
3.  **Extraction Modes**:
    - `extraction_mode`: `one-pass` (性能) / `multi-pass` (质量)。
    - `enable_cross_chunk_inference`: 是否开启昂贵的跨片段检查。

---

## 四、 核心 Prompt 设计精要

| 任务 | 核心指令 |
| :--- | :--- |
| **实体抽取** | "严格对齐 Schema"、"宁缺毋滥"、"输出 JSON entities 数组"。 |
| **关系抽取** | "严禁建立 Schema 以外的实体类型组合关系"。 |
| **消歧判断** | "名称相似度" + "类型一致性" + "上下文语义" 综合决策。 |
| **Graph RAG** | "优先使用图谱中的结构化关系进行回答"、"凡有依据处，结合原文片段说明"。 |

---

## 五、 后续优化方向 (Craft Optimization)

1.  **分层消歧**：引入聚类算法处理海量同名实体，减少 LLM 调用成本。
2.  **长短记忆结合**：在 Graph RAG 中引入历史对话的 `Summary` 作为长期记忆，支持多轮追问。
3.  **多模态增强**：通过 OCR 工艺提升对复杂文档（扫描件、表格）的结构化解析能力。
