"""
图谱数据模型
节点(Node)、边(Edge)及图谱整体数据结构
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class EvidenceQuote(BaseModel):
    """证据短句：将一条知识锚定到原文片段（AEVS / Wikontic / TRACE-KG 式 span 级溯源）"""
    chunk_id: str = Field(default="", description="来源片段ID")
    quote: str = Field(default="", description="支撑该知识的原文短句")


class Node(BaseModel):
    """图谱节点"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="节点ID")
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="附加属性")
    source_chunk_ids: List[str] = Field(default_factory=list, description="来源片段ID列表")
    evidence_quotes: List[Dict[str, Any]] = Field(default_factory=list, description="证据短句列表 [{chunk_id, quote}]")
    confidence: float = Field(default=1.0, description="置信度 0-1")


class Edge(BaseModel):
    """图谱边(关系)"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="边ID")
    source_id: str = Field(..., description="源节点ID")
    target_id: str = Field(..., description="目标节点ID")
    relation_type: str = Field(..., description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="附加属性")
    source_chunk_ids: List[str] = Field(default_factory=list, description="来源片段ID列表")
    evidence_quotes: List[Dict[str, Any]] = Field(default_factory=list, description="证据短句列表 [{chunk_id, quote}]")
    confidence: float = Field(default=1.0, description="置信度 0-1")


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
