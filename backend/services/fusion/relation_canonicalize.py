"""关系谓词规范化（DIAL-KG / Wikontic 模式）。

流程：
1. 统计草稿图谱中所有关系谓词及其频次；
2. 用相似度对谓词聚类（语义相近的谓词归为一组）；
3. 一次 LLM 调用为每个多成员簇裁决「规范谓词名」，
   若 Schema 已定义关系类型，则优先映射到 Schema 内谓词；
4. 重写草稿图谱中的边谓词，降低关系冗余。
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Dict, List, Optional

from config import load_config
from services.graph_store import load_draft_graph, save_draft_graph, _load_schema_dict
from services.fast_similarity import name_similarity_score
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)

RESERVED_RELATION_TYPES = {"下一段"}

CANON_PROMPT = """你是知识图谱关系规范化专家。下面是若干「语义相近的关系谓词簇」。
请为每个簇裁决一个**规范谓词名**（canonical），用于统一表达。

规则：
1. 若提供了 Schema 关系类型，优先从中选择最贴切的规范名；
2. 否则从簇内成员中选择最规范、最通用的一个，或给出更规范的命名；
3. 不要把语义不同的谓词强行合并。

## Schema 关系类型（可空）
{schema_relations}

## 关系谓词簇
{clusters}

## 输出（严格 JSON）
{{
  "decisions": [
    {{"cluster_id": 0, "canonical": "规范谓词名", "members": ["成员1", "成员2"]}}
  ]
}}
"""


def _cluster_predicates(predicates: List[str], threshold: float) -> List[List[str]]:
    """基于名称相似度对谓词做简单聚类（贪心并查集）。"""
    parent = {p: p for p in predicates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(predicates)):
        for j in range(i + 1, len(predicates)):
            a, b = predicates[i], predicates[j]
            if name_similarity_score(a, b) >= threshold:
                union(a, b)

    clusters: Dict[str, List[str]] = {}
    for p in predicates:
        root = find(p)
        clusters.setdefault(root, []).append(p)
    return [members for members in clusters.values()]


async def canonicalize_relations(project_id: str, threshold: Optional[float] = None) -> Dict:
    """对草稿图谱执行关系谓词规范化，返回统计。"""
    config = load_config()
    if threshold is None:
        threshold = float(getattr(config, "relation_canonicalize_threshold", 0.82))

    graph = load_draft_graph(project_id)
    schema = _load_schema_dict(project_id)
    schema_relations = [rt["name"] for rt in (schema or {}).get("relation_types", [])]

    # 统计谓词频次（排除系统保留谓词）
    pred_counter = Counter(
        e.relation_type for e in graph.edges
        if e.relation_type and e.relation_type not in RESERVED_RELATION_TYPES
    )
    predicates = list(pred_counter.keys())

    stats = {"distinct_predicates": len(predicates), "clusters_merged": 0, "edges_rewritten": 0, "llm_calls": 0}

    if len(predicates) < 2:
        return {**stats, "skipped": True}

    clusters = _cluster_predicates(predicates, threshold)
    multi_clusters = [c for c in clusters if len(c) > 1]
    if not multi_clusters:
        return {**stats, "note": "无可合并的相近谓词"}

    # LLM 裁决规范名（一次调用）
    cluster_payload = [
        {"cluster_id": idx, "members": members, "frequencies": {m: pred_counter[m] for m in members}}
        for idx, members in enumerate(multi_clusters)
    ]
    mapping: Dict[str, str] = {}
    try:
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是严谨的关系规范化器，只返回 JSON。"},
                {"role": "user", "content": CANON_PROMPT.format(
                    schema_relations=json.dumps(schema_relations, ensure_ascii=False),
                    clusters=json.dumps(cluster_payload, ensure_ascii=False, indent=2),
                )},
            ],
            complexity=COMPLEXITY_NORMAL,
        )
        stats["llm_calls"] += 1
        for dec in result.get("decisions", []):
            canonical = (dec.get("canonical") or "").strip()
            members = dec.get("members") or []
            if not canonical:
                continue
            for m in members:
                if m and m != canonical:
                    mapping[m] = canonical
    except Exception as e:
        logger.warning(f"[关系规范化] LLM 裁决失败，回退到频次众数规则: {e}")
        # 回退：每簇取频次最高者为规范名
        for members in multi_clusters:
            canonical = max(members, key=lambda m: pred_counter[m])
            for m in members:
                if m != canonical:
                    mapping[m] = canonical

    if not mapping:
        return {**stats, "note": "未产生合并映射"}

    # 重写边谓词
    rewritten = 0
    for e in graph.edges:
        if e.relation_type in mapping:
            e.relation_type = mapping[e.relation_type]
            rewritten += 1

    save_draft_graph(project_id, graph)
    stats["clusters_merged"] = len(set(mapping.values()))
    stats["edges_rewritten"] = rewritten
    stats["mapping"] = mapping
    logger.info(f"[关系规范化] 完成: {stats}")
    return stats
