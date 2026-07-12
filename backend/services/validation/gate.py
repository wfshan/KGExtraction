"""发布前确定性 Schema 校验门控（CES 执行层）。

校验项（全部确定性、无 LLM）：
1. 实体类型必须在 Schema 中（或系统保留类型）
2. 关系类型必须在 Schema 中（或系统保留类型）
3. 边的 source/target 必须指向存在的节点（无悬挂边）
4. 关系两端实体类型必须满足 Schema 的 source/target 约束（支持多类型约束）
5. 重复边检测（同 source/target/relation 仅保留一条）
6. 可选：关系必须具备至少一条**已验证**证据短句（publish_gate_require_evidence）。
   证据的 verified 标记由抽取时的确定性原文匹配产生——门控裁决的是
   「证据真的在原文里」，而不只是「有证据字段」。

返回结构化报告：哪些节点/边通过、哪些被拒绝及原因。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from models.graph import GraphData, Node, Edge
from services.schema_utils import (
    relation_source_types,
    relation_target_types,
    type_satisfies,
    format_constraint,
)
from services.evidence import has_verified_evidence
# 单一来源：保留类型集中定义在 graph_store，避免多处漂移
from services.graph_store import RESERVED_ENTITY_TYPES, RESERVED_RELATION_TYPES


@dataclass
class ValidationViolation:
    """单条校验违规记录"""
    kind: str            # "node" | "edge"
    target_id: str
    rule: str            # 违反的规则标识
    message: str

    def to_dict(self) -> Dict:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "rule": self.rule,
            "message": self.message,
        }


@dataclass
class ValidationReport:
    """校验报告"""
    passed: bool = True
    valid_node_ids: Set[str] = field(default_factory=set)
    valid_edge_ids: Set[str] = field(default_factory=set)
    violations: List[ValidationViolation] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "valid_node_count": len(self.valid_node_ids),
            "valid_edge_count": len(self.valid_edge_ids),
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
            "stats": self.stats,
        }


def _load_schema_sets(schema: Optional[Dict]) -> Tuple[Set[str], Set[str], Dict[str, Dict]]:
    """从 schema dict 提取实体类型集合、关系类型集合、关系定义映射。"""
    if not schema:
        return set(), set(), {}
    entity_types = {et["name"] for et in schema.get("entity_types", []) if et.get("name")}
    relation_defs = {rt["name"]: rt for rt in schema.get("relation_types", []) if rt.get("name")}
    relation_types = set(relation_defs.keys())
    return entity_types, relation_types, relation_defs


def validate_for_publish(
    graph: GraphData,
    schema: Optional[Dict],
    require_evidence: bool = False,
) -> ValidationReport:
    """对草稿图谱执行确定性校验，返回可发布的节点/边集合与违规清单。"""
    report = ValidationReport()
    entity_types, relation_types, relation_defs = _load_schema_sets(schema)

    node_by_id: Dict[str, Node] = {n.id: n for n in graph.nodes}

    # ---- 节点校验 ----
    for node in graph.nodes:
        if entity_types and node.entity_type not in entity_types and node.entity_type not in RESERVED_ENTITY_TYPES:
            report.violations.append(ValidationViolation(
                kind="node",
                target_id=node.id,
                rule="entity_type_not_in_schema",
                message=f"节点「{node.name}」的类型「{node.entity_type}」不在 Schema 中",
            ))
            continue
        report.valid_node_ids.add(node.id)

    # ---- 边校验 ----
    seen_edge_keys: Set[Tuple[str, str, str]] = set()
    for edge in graph.edges:
        # 悬挂边：端点不存在或端点节点本身未通过校验
        if edge.source_id not in report.valid_node_ids or edge.target_id not in report.valid_node_ids:
            report.violations.append(ValidationViolation(
                kind="edge",
                target_id=edge.id,
                rule="dangling_edge",
                message=f"边「{edge.relation_type}」引用了不存在或未通过校验的端点",
            ))
            continue

        # 关系类型必须在 Schema 中
        if relation_types and edge.relation_type not in relation_types and edge.relation_type not in RESERVED_RELATION_TYPES:
            report.violations.append(ValidationViolation(
                kind="edge",
                target_id=edge.id,
                rule="relation_type_not_in_schema",
                message=f"边的关系类型「{edge.relation_type}」不在 Schema 中",
            ))
            continue

        # 关系两端类型约束（保留关系类型不做类型约束；支持多类型约束）
        if edge.relation_type in relation_defs:
            rt_def = relation_defs[edge.relation_type]
            s_node = node_by_id.get(edge.source_id)
            t_node = node_by_id.get(edge.target_id)
            src_constraint = relation_source_types(rt_def)
            tgt_constraint = relation_target_types(rt_def)
            if s_node and not type_satisfies(s_node.entity_type, src_constraint):
                report.violations.append(ValidationViolation(
                    kind="edge",
                    target_id=edge.id,
                    rule="source_type_mismatch",
                    message=f"关系「{edge.relation_type}」要求源类型为「{format_constraint(rt_def.get('source_entity_type'))}」，实际为「{s_node.entity_type}」",
                ))
                continue
            if t_node and not type_satisfies(t_node.entity_type, tgt_constraint):
                report.violations.append(ValidationViolation(
                    kind="edge",
                    target_id=edge.id,
                    rule="target_type_mismatch",
                    message=f"关系「{edge.relation_type}」要求目标类型为「{format_constraint(rt_def.get('target_entity_type'))}」，实际为「{t_node.entity_type}」",
                ))
                continue

        # 重复边检测
        edge_key = (edge.source_id, edge.target_id, edge.relation_type)
        if edge_key in seen_edge_keys:
            report.violations.append(ValidationViolation(
                kind="edge",
                target_id=edge.id,
                rule="duplicate_edge",
                message=f"重复关系「{edge.relation_type}」（同源同目标）已存在",
            ))
            continue
        seen_edge_keys.add(edge_key)

        # 可选：证据要求（保留关系类型如「下一段」豁免）。
        # 有证据短句时必须至少一条经确定性验证命中原文（verified 非 False）；
        # 无证据短句时退回来源片段兜底（兼容未开启证据锚定的旧数据）。
        if require_evidence and edge.relation_type not in RESERVED_RELATION_TYPES:
            quotes = getattr(edge, "evidence_quotes", None) or []
            if quotes:
                if not has_verified_evidence(quotes):
                    report.violations.append(ValidationViolation(
                        kind="edge",
                        target_id=edge.id,
                        rule="unverified_evidence",
                        message=f"关系「{edge.relation_type}」的证据短句未能命中原文（疑似幻觉证据）",
                    ))
                    continue
            elif not edge.source_chunk_ids:
                report.violations.append(ValidationViolation(
                    kind="edge",
                    target_id=edge.id,
                    rule="missing_evidence",
                    message=f"关系「{edge.relation_type}」缺少证据短句/来源片段",
                ))
                continue

        report.valid_edge_ids.add(edge.id)

    report.passed = len(report.violations) == 0
    report.stats = {
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
        "valid_nodes": len(report.valid_node_ids),
        "valid_edges": len(report.valid_edge_ids),
        "rejected_nodes": len(graph.nodes) - len(report.valid_node_ids),
        "rejected_edges": len(graph.edges) - len(report.valid_edge_ids),
    }
    return report
