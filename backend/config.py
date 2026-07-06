"""
系统配置管理模块
管理 LLM API Key、Base URL 等全局配置，持久化到 JSON 文件。
"""
import json
import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# 数据存储根目录
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "system_config.json"


class SystemConfig(BaseModel):
    """系统全局配置"""
    model_config = {"protected_namespaces": ()}

    api_key: str = Field(default="", description="大模型 API Key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="大模型 API Base URL (OpenAI 兼容)"
    )
    # 模型路由配置
    model_simple: str = Field(default="qwen-turbo", description="轻量模型 - 简单任务")
    model_normal: str = Field(default="qwen-plus", description="均衡模型 - 常规任务")
    model_complex: str = Field(default="qwen-max", description="强力模型 - 复杂任务")
    model_embedding: str = Field(default="text-embedding-v1", description="向量模型 (仅在本地模型加载失败时使用)")
    similarity_backend: str = Field(
        default="keyword",
        description="相似度后端 (keyword/vector)，keyword 为全链路非向量检索"
    )
    # 向量检索参数
    vector_top_k: int = Field(default=20, description="向量召回 Top-K")
    score_threshold: float = Field(default=0.5, description="相似度阈值门控")
    fast_score_threshold: float = Field(default=0.25, description="快速相似度检索阈值")
    # 分片参数
    chunk_size: int = Field(default=500, description="文本分片大小(字符)")
    chunk_overlap: int = Field(default=100, description="分片重叠(字符)")
    # 并发参数
    parallel_processes: int = Field(default=5, ge=1, le=20, description="抽取任务并发数（1-20）")
    # 性能优化参数
    extraction_mode: str = Field(default="one-pass", description="抽取模式 (one-pass/multi-pass)")
    enable_self_correction: bool = Field(default=False, description="是否启用自我修正")
    enable_cross_chunk_inference: bool = Field(default=False, description="是否启用跨片段关系推断")
    enable_disambiguation: bool = Field(default=True, description="是否启用实体消歧")
    disambiguation_fast_path_score: float = Field(default=0.92, description="实体消歧快速直通分数阈值")
    disambiguation_candidate_limit_per_entity: int = Field(default=8, description="每个实体参与消歧的候选上限")
    disambiguation_low_confidence_only: bool = Field(default=False, description="是否仅对低置信实体执行 LLM 消歧")
    disambiguation_entity_confidence_threshold: float = Field(default=0.85, description="低置信阈值（低于该值才参与消歧）")
    llm_stream_log: bool = Field(default=False, description="是否以流式方式写入抽取日志")
    database_batch_size: int = Field(default=10, description="数据库批量写入大小")

    # ===== 演进能力开关（2026 可治理知识工程工作台）=====
    # 证据锚定：抽取时要求 LLM 返回支撑实体/关系的原文短句
    enable_evidence_anchor: bool = Field(default=True, description="是否在抽取时锚定原文证据短句")
    # 确定性发布门控（Cognitive-Executive Separation）
    enable_publish_gate: bool = Field(default=True, description="发布前启用确定性 Schema 校验门控")
    publish_gate_block: bool = Field(default=False, description="门控为阻断模式（True=不通过则发布失败；False=过滤不合规项后发布）")
    publish_gate_require_evidence: bool = Field(default=False, description="发布门控要求关系具备至少一条证据短句")
    # 后验本体修正（OAK+MEND）
    enable_post_correction: bool = Field(default=False, description="抽取完成后自动执行后验本体批量修正")
    post_correction_batch_size: int = Field(default=40, description="后验修正单批送入 LLM 的疑似违规条目数")
    # Graph RAG 意图路由
    enable_intent_routing: bool = Field(default=True, description="问图时启用意图识别路由种子实体")
    # 关系谓词规范化
    relation_canonicalize_threshold: float = Field(default=0.82, description="关系谓词聚类合并的相似度阈值")
    # 实体聚类融合
    entity_cluster_threshold: float = Field(default=0.88, description="批次实体聚类合并的相似度阈值")
    # 社区摘要
    enable_community_on_publish: bool = Field(default=False, description="发布时自动构建社区摘要")
    community_max_summary_nodes: int = Field(default=30, description="社区摘要单社区参与 LLM 的最大节点数")


def load_config() -> SystemConfig:
    """加载系统配置，优先从文件读取，不存在则从环境变量初始化"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SystemConfig(**data)

    # 从环境变量初始化
    config = SystemConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        base_url=os.getenv("BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    save_config(config)
    return config


def save_config(config: SystemConfig) -> None:
    """保存系统配置到 JSON 文件"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)


def get_project_dir(project_id: str) -> Path:
    """获取项目数据目录"""
    project_dir = DATA_DIR / "projects" / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir
