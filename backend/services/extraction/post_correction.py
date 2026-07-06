"""后验本体修正（OAK + MEND 模式）。

不同于逐条 LLM 校验（高成本），本模块：
1. 用确定性规则一次性检测整张草稿图谱的本体违规；
2. 将「类型不在 Schema 中」的实体/关系按批送入 LLM，**一次调用修正一批**，
   要求模型将其重映射到最相近的合法类型，或判定为应删除；
3. 类型约束不匹配（source/target）的边，确定性地直接移除。

显著降低 token 消耗，适合作为 self_correct 的发布前升级版。
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from config import load_config
from models.graph import GraphData
from services.graph_store import load_draft_graph, save_draft_graph, _load_schema_dict
from services.validation import validate_for_publish
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)


REMAP_ENTITY_PROMPT = """你是知识图谱本体修正专家。下面是一批实体，它们的类型不在目标 Schema 中。
请将每个实体重映射到 Schema 中**语义最接近的合法实体类型**；若没有任何合法类型适配，则标记 action=remove。

## 合法实体类型
{entity_types}

## 待修正实体（含原类型）
{items}

## 输出（严格 JSON）
{{
  "fixes": [
    {{"name": "实体名称", "action": "remap" 或 "remove", "new_entity_type": "合法类型（remap 时必填）"}}
  ]
}}
"""

REMAP_RELATION_PROMPT = """你是知识图谱本体修正专家。下面是一批关系，它们的关系类型不在目标 Schema 中。
请将每个关系重映射到 Schema 中**语义最接近的合法关系类型**；若没有任何合法类型适配，则标记 action=remove。

## 合法关系类型（含 源类型→目标类型 约束）
{relation_types}

## 待修正关系
{items}

## 输出（严格 JSON）
{{
  "fixes": [
    {{"key": "关系唯一key", "action": "remap" 或 "remove", "new_relation_type": "合法类型（remap 时必填）"}}
  ]
}}
"""


async def post_extraction_correction(project_id: str, batch_size: Optional[int] = None) -> Dict:
    """对草稿图谱执行后验本体批量修正，返回统计。"""
    config = load_config()
    if batch_size is None:
        batch_size = int(getattr(config, "post_correction_batch_size", 40))

    graph = load_draft_graph(project_id)
    schema = _load_schema_dict(project_id)
    if not schema:
        return {"skipped": True, "reason": "无 Schema，跳过后验修正"}

    entity_type_names = [et["name"] for et in schema.get("entity_types", [])]
    relation_defs = {rt["name"]: rt for rt in schema.get("relation_types", [])}

    report = validate_for_publish(graph, schema, require_evidence=False)

    node_by_id = {n.id: n for n in graph.nodes}
    edge_by_id = {e.id: e for e in graph.edges}

    # 收集违规
    bad_entity_nodes = []
    bad_relation_edges = []
    remove_edge_ids = set()

    for v in report.violations:
        if v.kind == "node" and v.rule == "entity_type_not_in_schema":
            node = node_by_id.get(v.target_id)
            if node:
                bad_entity_nodes.append(node)
        elif v.kind == "edge":
            edge = edge_by_id.get(v.target_id)
            if not edge:
                continue
            if v.rule == "relation_type_not_in_schema":
                bad_relation_edges.append(edge)
            elif v.rule in ("source_type_mismatch", "target_type_mismatch"):
                # 类型约束不匹配：确定性移除（认知层提案不可信）
                remove_edge_ids.add(edge.id)

    stats = {
        "entity_violations": len(bad_entity_nodes),
        "relation_violations": len(bad_relation_edges),
        "entities_remapped": 0,
        "entities_removed": 0,
        "relations_remapped": 0,
        "relations_removed": 0,
        "edges_removed_constraint": len(remove_edge_ids),
        "llm_calls": 0,
    }

    # ---- 批量修正实体类型 ----
    remove_node_ids = set()
    if bad_entity_nodes and entity_type_names:
        for i in range(0, len(bad_entity_nodes), batch_size):
            batch = bad_entity_nodes[i:i + batch_size]
            items = [{"name": n.name, "current_type": n.entity_type} for n in batch]
            try:
                result = await llm_gateway.chat_json(
                    messages=[
                        {"role": "system", "content": "你是严谨的本体修正器，只返回 JSON。"},
                        {"role": "user", "content": REMAP_ENTITY_PROMPT.format(
                            entity_types=json.dumps(entity_type_names, ensure_ascii=False),
                            items=json.dumps(items, ensure_ascii=False, indent=2),
                        )},
                    ],
                    complexity=COMPLEXITY_NORMAL,
                )
                stats["llm_calls"] += 1
                fixes = {f.get("name"): f for f in result.get("fixes", []) if f.get("name")}
                for n in batch:
                    fix = fixes.get(n.name)
                    if not fix:
                        continue
                    if fix.get("action") == "remap" and fix.get("new_entity_type") in entity_type_names:
                        n.entity_type = fix["new_entity_type"]
                        stats["entities_remapped"] += 1
                    elif fix.get("action") == "remove":
                        remove_node_ids.add(n.id)
                        stats["entities_removed"] += 1
            except Exception as e:
                logger.warning(f"[后验修正] 实体批次修正失败: {e}")

    # ---- 批量修正关系类型 ----
    if bad_relation_edges and relation_defs:
        from services.schema_utils import format_constraint
        rel_type_desc = [
            {"name": rt, "source": format_constraint(d.get("source_entity_type")), "target": format_constraint(d.get("target_entity_type"))}
            for rt, d in relation_defs.items()
        ]
        for i in range(0, len(bad_relation_edges), batch_size):
            batch = bad_relation_edges[i:i + batch_size]
            items = []
            for e in batch:
                s = node_by_id.get(e.source_id)
                t = node_by_id.get(e.target_id)
                items.append({
                    "key": e.id,
                    "current_type": e.relation_type,
                    "source": s.name if s else e.source_id,
                    "source_type": s.entity_type if s else "",
                    "target": t.name if t else e.target_id,
                    "target_type": t.entity_type if t else "",
                })
            try:
                result = await llm_gateway.chat_json(
                    messages=[
                        {"role": "system", "content": "你是严谨的本体修正器，只返回 JSON。"},
                        {"role": "user", "content": REMAP_RELATION_PROMPT.format(
                            relation_types=json.dumps(rel_type_desc, ensure_ascii=False, indent=2),
                            items=json.dumps(items, ensure_ascii=False, indent=2),
                        )},
                    ],
                    complexity=COMPLEXITY_NORMAL,
                )
                stats["llm_calls"] += 1
                fixes = {f.get("key"): f for f in result.get("fixes", []) if f.get("key")}
                for e in batch:
                    fix = fixes.get(e.id)
                    if not fix:
                        remove_edge_ids.add(e.id)
                        continue
                    if fix.get("action") == "remap" and fix.get("new_relation_type") in relation_defs:
                        e.relation_type = fix["new_relation_type"]
                        stats["relations_remapped"] += 1
                    else:
                        remove_edge_ids.add(e.id)
                        stats["relations_removed"] += 1
            except Exception as ex:
                logger.warning(f"[后验修正] 关系批次修正失败: {ex}")

    # ---- 应用修改 ----
    graph.nodes = [n for n in graph.nodes if n.id not in remove_node_ids]
    graph.edges = [
        e for e in graph.edges
        if e.id not in remove_edge_ids
        and e.source_id not in remove_node_ids
        and e.target_id not in remove_node_ids
    ]
    save_draft_graph(project_id, graph)

    stats["final_nodes"] = len(graph.nodes)
    stats["final_edges"] = len(graph.edges)
    logger.info(f"[后验修正] 完成: {stats}")
    return stats
