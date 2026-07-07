"""抽取计划（Plan）数据模型 —— v3「本体驱动的可编译抽取流程」。

对应产品设计文档第六章的三份数据契约：
- 契约 A：KnowledgeTypeMeta（知识类型元描述，surface/normalized/inductive 三态）
- 契约 B：Primitive（步骤库原语枚举，封闭集合，规划器只能选不能造）
- 契约 C：Plan / Step（抽取计划，步骤的 DAG）

第①步（本提交）只用它来「反编译」当前固定流水线为一份等价的默认 Plan，
外部行为零变更；执行器暂时把 Plan 当作「结构化的抽取配置」解析回参数，
真正的「按 Plan 遍历原语执行」在第②步落地。
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Abstractness(str, Enum):
    """知识类型在「具体↔抽象」谱系上的定位（契约 A）。"""
    surface = "surface"        # 表面存在、可定位：人名/文件名/账号
    normalized = "normalized"  # 表面存在、需标准化：日期→ISO / 金额→数值
    inductive = "inductive"    # 原文不存在、需归纳：规则/概念/模式


class EvidenceMode(str, Enum):
    """证据锚定方式（契约 A）。"""
    verbatim = "verbatim"  # 证据短句逐字命中原文
    span = "span"          # 锚定支撑归纳的源案例区间
    none = "none"          # 不要求证据


class Primitive(str, Enum):
    """步骤库原语（契约 B）——封闭集合。规划器只能从中选择，不能发明。

    分组见设计文档 6.5。标注 [过渡] 的是为「忠实反编译当前流水线」而设的
    现状原语，第②步会被规范化/拆分为正式原语。
    """
    # 分片
    segment = "segment"
    select_scope = "select_scope"
    # 候选生成
    extract_surface = "extract_surface"
    normalize_value = "normalize_value"
    induce_from_cases = "induce_from_cases"
    aggregate_then_induce = "aggregate_then_induce"
    extract_combined = "extract_combined"          # [过渡] one-pass 合并抽取实体+关系
    # 关系
    extract_relations_intra = "extract_relations_intra"
    infer_relations_cross = "infer_relations_cross"
    link_to_existing = "link_to_existing"
    schema_driven_linking = "schema_driven_linking"  # [过渡] 存量 Schema 驱动关系链接
    # 对齐精炼
    resolve_surface = "resolve_surface"
    merge_semantic = "merge_semantic"
    canonicalize_predicate = "canonicalize_predicate"
    # 校验组织
    validate_type = "validate_type"
    validate_structure = "validate_structure"
    verify_evidence_verbatim = "verify_evidence_verbatim"
    verify_faithfulness = "verify_faithfulness"
    self_correct = "self_correct"                  # [过渡] v2 逐片段自我修正（LLM）
    post_correct = "post_correct"                  # [过渡] OAK+MEND 后验本体批量修正
    build_hierarchy = "build_hierarchy"
    detect_conflict = "detect_conflict"
    # 组织
    add_document_structure = "add_document_structure"  # [过渡] 文档片段锚点 + 「下一段」边


class KnowledgeTypeMeta(BaseModel):
    """知识类型元描述（契约 A）。作为规划器的输入信号。

    第①步默认反编译时，所有类型均标为 surface（忠实反映现状：现状不区分
    抽象度），这也正是现状对「归纳型知识」无能为力的根因所在。
    """
    name: str
    abstractness: Abstractness = Abstractness.surface
    evidence_mode: EvidenceMode = EvidenceMode.verbatim
    identity_by: str = "name"                 # name | semantic
    structure_template: Optional[Dict[str, Any]] = None
    granularity_hint: str = ""


class Step(BaseModel):
    """抽取计划中的一个步骤实例（契约 C）。"""
    step_id: str
    primitive: Primitive
    targets: List[str] = Field(default_factory=list)   # 空 = 作用于全部类型
    params: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    reason: str = ""


class Plan(BaseModel):
    """抽取计划（契约 C）——步骤的 DAG，编译产物，可读/可编辑/可版本化/可复现。"""
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    schema_version: int = 0
    source: str = "planner-default"           # planner-default | planner-llm | human-edited
    validated: bool = False
    knowledge_types: Dict[str, KnowledgeTypeMeta] = Field(default_factory=dict)
    steps: List[Step] = Field(default_factory=list)

    def step_by_id(self, step_id: str) -> Optional[Step]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def has_primitive(self, primitive: Primitive) -> bool:
        return any(s.primitive == primitive for s in self.steps)
