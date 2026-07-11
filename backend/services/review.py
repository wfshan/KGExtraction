"""人工复核服务：增量 diff + 置信度排序队列 + 逐项裁决。

复核成本必须随「变更量」而非「图谱总量」增长，否则治理流程在规模化时崩塌。
因此复核对象是 draft 相对 published 的差异（新增/变更），并按风险排序：
门控违规 > 未验证证据 > 低置信度。
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from services.graph_store import (
    load_draft_graph,
    load_published_graph,
    save_draft_graph,
    _load_schema_dict,
    is_doc_layer_node,
    is_doc_layer_edge,
)
from services.evidence import has_verified_evidence
from services.audit import record_audit

logger = logging.getLogger(__name__)


def build_review_queue(project_id: str, run_id: Optional[str] = None, include_doc_layer: bool = False) -> Dict:
    """构建复核队列：draft 相对 published 的新增/变更项，按风险排序。"""
    draft = load_draft_graph(project_id)
    published = load_published_graph(project_id)
    schema = _load_schema_dict(project_id)

    # 实体类型 → 抽象度映射：让复核队列按抽象度分别呈现证据语义与可信度口径
    # （表面知识看「逐字证据是否命中」，归纳知识看「忠实度校验 + 支撑案例数」）。
    abstractness_by_type: Dict[str, str] = {}
    if schema:
        for et in schema.get("entity_types", []):
            abstractness_by_type[et.get("name", "")] = et.get("abstractness", "surface") or "surface"

    pub_nodes = {n.id: n for n in published.nodes}
    pub_edges = {e.id: e for e in published.edges}
    draft_node_by_id = {n.id: n for n in draft.nodes}

    # 门控预演：违规项在队列中高亮并给出原因
    violations_by_id: Dict[str, List[str]] = {}
    try:
        from services.validation import validate_for_publish
        report = validate_for_publish(draft, schema)
        for v in report.violations:
            violations_by_id.setdefault(v.target_id, []).append(v.message)
    except Exception as e:
        logger.warning(f"[Review] 门控预演失败: {e}")

    items: List[Dict] = []

    for node in draft.nodes:
        if not include_doc_layer and is_doc_layer_node(node.entity_type):
            continue
        if run_id and node.run_id != run_id:
            continue
        pub = pub_nodes.get(node.id)
        if pub and pub.name == node.name and pub.entity_type == node.entity_type:
            change = "unchanged"
        elif pub:
            change = "changed"
        else:
            change = "new"
        if change == "unchanged":
            continue
        review_state = (node.properties or {}).get("_review", {})
        abstractness = abstractness_by_type.get(node.entity_type, "surface")
        # 归纳知识的支撑案例数 = 去重后来源片段数（同一 (类型,名称) 被多个片段归纳出即累加）
        support_cases = len([c for c in node.source_chunk_ids if c != "cold_start"])
        items.append({
            "kind": "node",
            "id": node.id,
            "title": node.name,
            "entity_type": node.entity_type,
            "abstractness": abstractness,
            "confidence": node.confidence,
            "support_cases": support_cases,
            "run_id": node.run_id,
            "change": change,
            "violations": violations_by_id.get(node.id, []),
            "evidence_verified": has_verified_evidence(node.evidence_quotes),
            "evidence_quotes": node.evidence_quotes[:3],
            "source_chunk_count": len(node.source_chunk_ids),
            "review_status": review_state.get("status", "pending"),
        })

    for edge in draft.edges:
        if not include_doc_layer and is_doc_layer_edge(edge.relation_type):
            continue
        if run_id and edge.run_id != run_id:
            continue
        pub = pub_edges.get(edge.id)
        if pub and pub.relation_type == edge.relation_type:
            continue  # 已发布且未变更
        s = draft_node_by_id.get(edge.source_id)
        t = draft_node_by_id.get(edge.target_id)
        review_state = (edge.properties or {}).get("_review", {})
        items.append({
            "kind": "edge",
            "id": edge.id,
            "title": f"{s.name if s else '?'} --[{edge.relation_type}]--> {t.name if t else '?'}",
            "relation_type": edge.relation_type,
            "abstractness": "surface",
            "confidence": edge.confidence,
            "support_cases": len([c for c in edge.source_chunk_ids if c != "cold_start"]),
            "run_id": edge.run_id,
            "change": "changed" if pub else "new",
            "violations": violations_by_id.get(edge.id, []),
            "evidence_verified": has_verified_evidence(edge.evidence_quotes),
            "evidence_quotes": edge.evidence_quotes[:3],
            "source_chunk_count": len(edge.source_chunk_ids),
            "review_status": review_state.get("status", "pending"),
        })

    # 风险排序：有门控违规 > 证据未验证 > 低置信度
    def risk_key(it: Dict):
        return (
            0 if it["violations"] else 1,
            0 if not it["evidence_verified"] else 1,
            it.get("confidence", 1.0),
        )
    items.sort(key=risk_key)

    pending = [it for it in items if it["review_status"] == "pending"]
    return {
        "total": len(items),
        "pending": len(pending),
        "with_violations": sum(1 for it in items if it["violations"]),
        "unverified_evidence": sum(1 for it in items if not it["evidence_verified"]),
        "items": items,
    }


def decide_review(project_id: str, kind: str, target_id: str, decision: str, actor: str, reason: str = "") -> Dict:
    """对单个复核项做出裁决：approve（标记通过）或 reject（从草稿删除）。"""
    if decision not in ("approve", "reject"):
        raise ValueError("decision 必须为 approve 或 reject")

    graph = load_draft_graph(project_id)
    now = datetime.now().isoformat()

    if kind == "node":
        target = next((n for n in graph.nodes if n.id == target_id), None)
        if not target:
            raise ValueError("节点不存在")
        if decision == "approve":
            target.properties["_review"] = {"status": "approved", "actor": actor, "ts": now}
        else:
            graph.nodes = [n for n in graph.nodes if n.id != target_id]
            removed_edges = [e.id for e in graph.edges if e.source_id == target_id or e.target_id == target_id]
            graph.edges = [e for e in graph.edges if e.source_id != target_id and e.target_id != target_id]
            try:
                from services.reflection import record_case
                record_case(project_id, "entity", "delete",
                            {"name": target.name, "entity_type": target.entity_type})
            except Exception:
                pass
    elif kind == "edge":
        target = next((e for e in graph.edges if e.id == target_id), None)
        if not target:
            raise ValueError("关系不存在")
        if decision == "approve":
            target.properties["_review"] = {"status": "approved", "actor": actor, "ts": now}
        else:
            graph.edges = [e for e in graph.edges if e.id != target_id]
            try:
                from services.reflection import record_case
                record_case(project_id, "relation", "delete", {"relation_type": target.relation_type})
            except Exception:
                pass
    else:
        raise ValueError("kind 必须为 node 或 edge")

    save_draft_graph(project_id, graph)
    record_audit(
        project_id, actor, f"review_{decision}",
        target_kind=kind, target_id=target_id,
        detail={"reason": reason, "title": getattr(target, "name", None) or getattr(target, "relation_type", "")},
    )
    return {"message": f"已{('通过' if decision == 'approve' else '拒绝')}", "kind": kind, "id": target_id}
