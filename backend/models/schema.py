"""
Schema (本体) 数据模型
定义图谱的实体类型和关系类型
"""
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class EntityType(BaseModel):
    """实体类型定义

    v3 契约 A：新增抽取语义字段，决定该类型走哪条子流程。默认值使旧数据
    自动落在"表面抽取 + 逐字证据"，行为与 v2 一致。
    """
    name: str = Field(..., description="实体类型名称，如：人物、组织、地点")
    definition: str = Field(default="", description="语义定义，帮助模型理解概念")
    examples: List[str] = Field(default_factory=list, description="示例实例列表")
    color: str = Field(default="#4A90D9", description="可视化颜色")
    # v3 抽取语义
    abstractness: str = Field(default="surface", description="抽象度: surface(表面存在) | normalized(需标准化) | inductive(需归纳)")
    evidence_mode: str = Field(default="verbatim", description="证据模式: verbatim(逐字命中) | span(源案例区间) | none")
    structure_template: Optional[Dict[str, Any]] = Field(default=None, description="inductive 类型的归纳产物结构模板 {fields:[{key,required,description}]}")
    identity_by: str = Field(default="name", description="去重口径: name(表面名) | semantic(语义等价)")


class RelationType(BaseModel):
    """关系类型定义

    source/target 约束支持单类型（str）或多类型（List[str]），
    空值表示不约束。现实本体中同一谓词常有多个合法 domain/range
    （如「位于」适用于 公司→城市 与 人物→城市），无需复制关系类型。
    """
    name: str = Field(..., description="关系类型名称，如：就职于、位于")
    definition: str = Field(default="", description="语义定义")
    source_entity_type: Union[str, List[str]] = Field(default="", description="源实体类型约束（str 或 List[str]，空=不约束）")
    target_entity_type: Union[str, List[str]] = Field(default="", description="目标实体类型约束（str 或 List[str]，空=不约束）")
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
