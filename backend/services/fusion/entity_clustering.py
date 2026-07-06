"""批次实体聚类融合（KGGen 模式）。

在 Run 结束或融合阶段，对草稿图谱中**同类型**实体按相似度聚类，
对每个候选簇用一次批量 LLM 裁决确认是否为同一实体并选定规范名，
随后合并节点（重定向边、合并来源片段与证据），降低稀疏度与逐条消歧成本。
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional

from config import load_config
from services.graph_store import load_draft_graph, save_draft_graph, _merge_evidence
from services.fast_similarity import name_similarity_score
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)

# 不参与聚类的系统保留类型
SKIP_ENTITY_TYPES = {"未归类片段", "文档片段", "未分类实体", "未知类型"}

CLUSTER_PROMPT = """你是实体消歧专家。下面是若干「同类型、名称相近的候选实体簇」。
请判断每个簇内的实体是否确为**同一现实实体**，并给出规范名。
只合并你有把握的；不确定的拆分或保持独立。

## 候选簇
{clusters}

## 输出（严格 JSON）
{{
  "decisions": [
    {{"cluster_id": 0, "merge": true, "canonical": "规范实体名", "members": ["成员1", "成员2"]}}
  ]
}}
"""


def _cluster_names(names: List[str], threshold: float) -> List[List[str]]:
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if name_similarity_score(names[i], names[j]) >= threshold:
                union(names[i], names[j])

    clusters: Dict[str, List[str]] = {}
    for n in names:
        clusters.setdefault(find(n), []).append(n)
    return [m for m in clusters.values() if len(m) > 1]


async def cluster_entities(project_id: str, threshold: Optional[float] = None, use_llm: bool = True) -> Dict:
    """批次实体聚类融合，返回统计。"""
    config = load_config()
    if threshold is None:
        threshold = float(getattr(config, "entity_cluster_threshold", 0.88))

    graph = load_draft_graph(project_id)
    nodes_by_name = {n.name: n for n in graph.nodes}

    # 按类型分组候选名
    names_by_type: Dict[str, List[str]] = defaultdict(list)
    for n in graph.nodes:
        if n.entity_type in SKIP_ENTITY_TYPES:
            continue
        names_by_type[n.entity_type].append(n.name)

    # 生成候选簇
    candidate_clusters: List[Dict] = []
    for etype, names in names_by_type.items():
        if len(names) < 2:
            continue
        for members in _cluster_names(names, threshold):
            candidate_clusters.append({"entity_type": etype, "members": members})

    stats = {"candidate_clusters": len(candidate_clusters), "merged_clusters": 0, "nodes_merged": 0, "edges_redirected": 0, "llm_calls": 0}
    if not candidate_clusters:
        return {**stats, "note": "无候选合并簇"}

    # LLM 批量确认（可选）
    confirmed: List[Dict] = []
    if use_llm:
        payload = [
            {"cluster_id": idx, "entity_type": c["entity_type"], "members": c["members"]}
            for idx, c in enumerate(candidate_clusters)
        ]
        try:
            result = await llm_gateway.chat_json(
                messages=[
                    {"role": "system", "content": "你是严谨的实体消歧器，只返回 JSON。"},
                    {"role": "user", "content": CLUSTER_PROMPT.format(
                        clusters=json.dumps(payload, ensure_ascii=False, indent=2)
                    )},
                ],
                complexity=COMPLEXITY_NORMAL,
            )
            stats["llm_calls"] += 1
            for dec in result.get("decisions", []):
                if not dec.get("merge"):
                    continue
                cid = dec.get("cluster_id")
                members = dec.get("members") or (candidate_clusters[cid]["members"] if isinstance(cid, int) and cid < len(candidate_clusters) else [])
                canonical = (dec.get("canonical") or (members[0] if members else "")).strip()
                if canonical and len(members) > 1:
                    confirmed.append({"canonical": canonical, "members": members})
        except Exception as e:
            logger.warning(f"[实体聚类] LLM 确认失败，回退为相似度直接合并: {e}")
            use_llm = False

    if not use_llm:
        for c in candidate_clusters:
            members = c["members"]
            # 规范名取来源片段最多的实体
            canonical = max(members, key=lambda m: len(nodes_by_name[m].source_chunk_ids) if m in nodes_by_name else 0)
            confirmed.append({"canonical": canonical, "members": members})

    if not confirmed:
        return {**stats, "note": "无确认合并"}

    # 执行合并
    id_redirect: Dict[str, str] = {}   # old_node_id -> canonical_node_id
    remove_node_ids = set()

    for cluster in confirmed:
        members = [m for m in cluster["members"] if m in nodes_by_name]
        if len(members) < 2:
            continue
        canonical_name = cluster["canonical"]
        canonical_node = nodes_by_name.get(canonical_name) or nodes_by_name[members[0]]
        canonical_node.name = cluster["canonical"]

        for m in members:
            node = nodes_by_name[m]
            if node.id == canonical_node.id:
                continue
            # 合并来源片段、证据、属性
            canonical_node.source_chunk_ids = list(set(canonical_node.source_chunk_ids + node.source_chunk_ids))
            canonical_node.evidence_quotes = _merge_evidence(canonical_node.evidence_quotes, node.evidence_quotes)
            for k, v in (node.properties or {}).items():
                canonical_node.properties.setdefault(k, v)
            id_redirect[node.id] = canonical_node.id
            remove_node_ids.add(node.id)
            stats["nodes_merged"] += 1
        stats["merged_clusters"] += 1

    # 重定向边并去重/去自环
    seen_edge_keys = set()
    new_edges = []
    for e in graph.edges:
        s = id_redirect.get(e.source_id, e.source_id)
        t = id_redirect.get(e.target_id, e.target_id)
        if s != e.source_id or t != e.target_id:
            stats["edges_redirected"] += 1
        if s == t:
            continue  # 合并后形成自环，丢弃
        key = (s, t, e.relation_type)
        if key in seen_edge_keys:
            continue
        seen_edge_keys.add(key)
        e.source_id = s
        e.target_id = t
        new_edges.append(e)

    graph.edges = new_edges
    graph.nodes = [n for n in graph.nodes if n.id not in remove_node_ids]
    save_draft_graph(project_id, graph)

    stats["final_nodes"] = len(graph.nodes)
    stats["final_edges"] = len(graph.edges)
    logger.info(f"[实体聚类] 完成: {stats}")
    return stats
