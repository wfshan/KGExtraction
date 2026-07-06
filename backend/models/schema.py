"""
Schema (本体) 数据模型
定义图谱的实体类型和关系类型
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class EntityType(BaseModel):
    """实体类型定义"""
    name: str = Field(..., description="实体类型名称，如：人物、组织、地点")
    definition: str = Field(default="", description="语义定义，帮助模型理解概念")
    examples: List[str] = Field(default_factory=list, description="示例实例列表")
    color: str = Field(default="#4A90D9", description="可视化颜色")


class RelationType(BaseModel):
    """关系类型定义"""
    name: str = Field(..., description="关系类型名称，如：就职于、位于")
    definition: str = Field(default="", description="语义定义")
    source_entity_type: str = Field(default="", description="源实体类型约束")
    target_entity_type: str = Field(default="", description="目标实体类型约束")
    examples: List[str] = Field(default_factory=list, description="示例关系列表")


class SchemaConfig(BaseModel):
    """Schema 配置"""
    entity_types: List[EntityType] = Field(default_factory=list, description="实体类型列表")
    relation_types: List[RelationType] = Field(default_factory=list, description="关系类型列表")


class SchemaSuggestionRequest(BaseModel):
    """Schema 建议请求"""
    sample_size: int = Field(default=15, description="采样文档片段数量（推荐15+获取全局概览）")
    source: str = Field(default="auto", description="数据源: auto/documents/graph")

class SchemaChatRequest(BaseModel):
    """Schema 多轮对话请求模型"""
    messages: List[dict] = Field(..., description="对话历史及其最新提问")
    source: str = Field(default="auto", description="数据源: auto/documents/graph")
