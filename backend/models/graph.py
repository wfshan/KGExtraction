"""
图谱数据模型
节点(Node)、边(Edge)及图谱整体数据结构

实体身份原则：节点/边的 ID 由内容确定性派生（而非随机 UUID），
使同一实体在多次抽取、多个版本之间保持稳定标识，支撑增量演化与外部引用。
"""
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator
import uuid


def make_node_id(entity_type: str, name: str) -> str:
    """节点稳定 ID：由 (entity_type, name) 确定性派生。"""
    digest = hashlib.sha1(f"{entity_type}␟{name}".encode("utf-8")).hexdigest()
    return f"n_{digest[:24]}"


def make_edge_id(source_id: str, relation_type: str, target_id: str) -> str:
    """边稳定 ID：由 (source, relation, target) 确定性派生。"""
    digest = hashlib.sha1(f"{source_id}␟{relation_type}␟{target_id}".encode("utf-8")).hexdigest()
    return f"e_{digest[:24]}"


class EvidenceQuote(BaseModel):
    """证据短句：将一条知识锚定到原文片段（AEVS / Wikontic / TRACE-KG 式 span 级溯源）"""
    chunk_id: str = Field(default="", description="来源片段ID")
    quote: str = Field(default="", description="支撑该知识的原文短句")
    verified: Optional[bool] = Field(default=None, description="是否经确定性校验命中原文（None=旧数据未校验）")


class Node(BaseModel):
    """图谱节点"""
    id: str = Field(default="", description="节点ID（缺省时由 entity_type+name 确定性派生）")
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="附加属性")
    source_chunk_ids: List[str] = Field(default_factory=list, description="来源片段ID列表")
    evidence_quotes: List[Dict[str, Any]] = Field(default_factory=list, description="证据短句列表 [{chunk_id, quote, verified}]")
    confidence: float = Field(default=1.0, description="置信度 0-1")
    run_id: str = Field(default="", description="产生该节点的抽取任务ID（人工创建/导入为空）")

    @model_validator(mode="after")
    def _ensure_stable_id(self):
        if not self.id:
            self.id = make_node_id(self.entity_type, self.name)
        return self


class Edge(BaseModel):
    """图谱边(关系)"""
    id: str = Field(default="", description="边ID（缺省时由 source+relation+target 确定性派生）")
    source_id: str = Field(..., description="源节点ID")
    target_id: str = Field(..., description="目标节点ID")
    relation_type: str = Field(..., description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="附加属性")
    source_chunk_ids: List[str] = Field(default_factory=list, description="来源片段ID列表")
    evidence_quotes: List[Dict[str, Any]] = Field(default_factory=list, description="证据短句列表 [{chunk_id, quote, verified}]")
    confidence: float = Field(default=1.0, description="置信度 0-1")
    run_id: str = Field(default="", description="产生该边的抽取任务ID（人工创建/导入为空）")

    @model_validator(mode="after")
    def _ensure_stable_id(self):
        if not self.id:
            self.id = make_edge_id(self.source_id, self.relation_type, self.target_id)
        return self


class GraphData(BaseModel):
    """图谱数据"""
    nodes: List[Node] = Field(default_factory=list, description="节点列表")
    edges: List[Edge] = Field(default_factory=list, description="边列表")
    version: int = Field(default=0, description="版本号")
    status: str = Field(default="draft", description="状态: draft/published")
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="最后更新时间"
    )
