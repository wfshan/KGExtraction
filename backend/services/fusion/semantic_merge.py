"""归纳知识语义归并（v3 深化）。

命名实体去重靠表面名相似（entity_clustering）；但归纳知识（规则/概念）措辞
千差万别、语义却可能相同（"集中转入分散转出" vs "短期资金快进快出"），表面
相似度无效。本模块对草稿图中 inductive 类型的节点，用 LLM 按**语义**分组、
合并同一条逻辑，并：

- 累积所有源案例（证据），使一条规则的支撑越充分越突出；
- 用**支撑案例数**重算置信度（客观频次），替代 LLM 自报的主观分数——
  这正是归纳知识"置信度客观锚"的落地。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, List

from config import load_config
from services.graph_store import load_draft_graph, save_draft_graph, _load_schema_dict, _merge_evidence
from services.llm_gateway import llm_gateway, COMPLEXITY_COMPLEX

logger = logging.getLogger(__name__)

# 支撑案例数达到该值即视为满信心
CONFIDENCE_SATURATION = 3.0

GROUP_PROMPT = """下面是同一类型「{etype}」的若干条**归纳知识**（措辞可能不同，但可能表达同一条逻辑/规则/概念）。
请把**表达同一条知识**的归为一组，并给出该组的**规范陈述**（canonical）。
只合并你有把握确为同一条的；不确定的请让它独立成组（members 只含自身）。

## 知识列表
{items}

## 输出（严格 JSON）
{{
  "groups": [
    {{"canonical": "该组的规范陈述", "members": ["成员陈述1", "成员陈述2"]}}
  ]
}}
"""


def _inductive_type_names(project_id: str) -> set:
    schema = _load_schema_dict(project_id) or {}
    return {
        et.get("name") for et in schema.get("entity_types", [])
        if et.get("abstractness") == "inductive" and et.get("name")
    }


def _confidence_from_cases(source_chunk_ids: List[str], fallback: float) -> float:
    n = len({c for c in (source_chunk_ids or []) if c and c != "cold_start"})
    if n <= 0:
        return fallback
    return round(min(1.0, n / CONFIDENCE_SATURATION), 3)


async def _group_semantic(etype: str, names: List[str]) -> List[Dict]:
    try:
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是知识归并专家，只返回 JSON。"},
                {"role": "user", "content": GROUP_PROMPT.format(
                    etype=etype,
                    items=json.dumps(names, ensure_ascii=False, indent=2),
                )},
            ],
            complexity=COMPLEXITY_COMPLEX,
        )
        return [g for g in result.get("groups", []) if g.get("members")]
    except Exception as e:
        logger.warning(f"[语义归并] 类型「{etype}」LLM 分组失败: {e}")
        return []


async def merge_inductive_knowledge(project_id: str) -> Dict:
    """对草稿图中 inductive 类型节点做语义归并，返回统计。"""
    inductive_types = _inductive_type_names(project_id)
    if not inductive_types:
        return {"note": "无 inductive 类型，跳过语义归并"}

    graph = load_draft_graph(project_id)
    nodes_by_name = {n.name: n for n in graph.nodes}

    by_type: Dict[str, List] = defaultdict(list)
    for n in graph.nodes:
        if n.entity_type in inductive_types:
            by_type[n.entity_type].append(n)

    stats = {
        "inductive_types": len(by_type),
        "candidate_nodes": sum(len(v) for v in by_type.values()),
        "groups_merged": 0, "nodes_merged": 0, "edges_redirected": 0, "llm_calls": 0,
    }

    id_redirect: Dict[str, str] = {}
    remove_node_ids = set()

    for etype, nodes in by_type.items():
        if len(nodes) < 2:
            continue
        groups = await _group_semantic(etype, [n.name for n in nodes])
        stats["llm_calls"] += 1
        for g in groups:
            members = [m for m in g.get("members", []) if m in nodes_by_name]
            if len(members) < 2:
                continue
            canonical_name = (g.get("canonical") or members[0]).strip() or members[0]
            canonical_node = nodes_by_name.get(canonical_name) or nodes_by_name[members[0]]
            canonical_node.name = canonical_name

            for m in members:
                node = nodes_by_name[m]
                if node.id == canonical_node.id:
                    continue
                canonical_node.source_chunk_ids = list(set(canonical_node.source_chunk_ids + node.source_chunk_ids))
                canonical_node.evidence_quotes = _merge_evidence(canonical_node.evidence_quotes, node.evidence_quotes)
                for k, v in (node.properties or {}).items():
                    canonical_node.properties.setdefault(k, v)
                id_redirect[node.id] = canonical_node.id
                remove_node_ids.add(node.id)
                stats["nodes_merged"] += 1
            stats["groups_merged"] += 1

    # 重定向边、去自环去重
    seen_edge_keys = set()
    new_edges = []
    for e in graph.edges:
        s = id_redirect.get(e.source_id, e.source_id)
        t = id_redirect.get(e.target_id, e.target_id)
        if s != e.source_id or t != e.target_id:
            stats["edges_redirected"] += 1
        if s == t:
            continue
        key = (s, t, e.relation_type)
        if key in seen_edge_keys:
            continue
        seen_edge_keys.add(key)
        e.source_id, e.target_id = s, t
        new_edges.append(e)

    graph.edges = new_edges
    graph.nodes = [n for n in graph.nodes if n.id not in remove_node_ids]

    # 置信度客观化：所有 inductive 节点按支撑案例数重算
    recalced = 0
    for n in graph.nodes:
        if n.entity_type in inductive_types:
            new_conf = _confidence_from_cases(n.source_chunk_ids, n.confidence)
            if new_conf != n.confidence:
                n.confidence = new_conf
                recalced += 1

    save_draft_graph(project_id, graph)
    stats["confidence_recalced"] = recalced
    stats["final_inductive_nodes"] = sum(1 for n in graph.nodes if n.entity_type in inductive_types)
    logger.info(f"[语义归并] 完成: {stats}")
    return stats
