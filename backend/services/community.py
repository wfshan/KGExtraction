"""社区检测与摘要（Microsoft GraphRAG 模式）。

在已发布图谱上做社区检测（优先 Leiden，缺依赖时回退 networkx 贪心模块度），
对每个社区用一次 LLM 调用生成主题标题与摘要，持久化供 `global` 检索模式使用。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx

from config import get_project_dir, load_config
from services.graph_store import get_nx_graph
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)


def _communities_file(project_id: str) -> Path:
    d = get_project_dir(project_id) / "communities"
    d.mkdir(exist_ok=True)
    return d / "communities.json"


def load_communities(project_id: str) -> List[Dict]:
    f = _communities_file(project_id)
    if not f.exists():
        return []
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def _detect_communities(G: nx.Graph) -> List[List[str]]:
    """返回社区列表（每个为 node_id 列表）。优先 Leiden。"""
    UG = G.to_undirected()
    if UG.number_of_nodes() == 0:
        return []

    # 尝试 Leiden（python-igraph + leidenalg）
    try:
        import igraph as ig  # type: ignore
        import leidenalg  # type: ignore

        nodes = list(UG.nodes())
        idx = {n: i for i, n in enumerate(nodes)}
        edges = [(idx[u], idx[v]) for u, v in UG.edges()]
        g = ig.Graph(n=len(nodes), edges=edges)
        part = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
        communities = []
        for comm in part:
            communities.append([nodes[i] for i in comm])
        logger.info(f"[社区] Leiden 检测到 {len(communities)} 个社区")
        return communities
    except Exception as e:
        logger.info(f"[社区] Leiden 不可用，回退 networkx 贪心模块度: {e}")

    try:
        from networkx.algorithms.community import greedy_modularity_communities
        comms = greedy_modularity_communities(UG)
        return [list(c) for c in comms]
    except Exception as e:
        logger.warning(f"[社区] networkx 社区检测失败，回退连通分量: {e}")
        return [list(c) for c in nx.connected_components(UG)]


SUMMARY_PROMPT = """下面是知识图谱中一个社区（聚类）的实体与关系。
请为该社区生成一个**主题标题**和一段**摘要**，概括该社区围绕什么主题、包含哪些关键实体与关系。

## 社区实体（部分）
{entities}

## 社区关系（部分）
{relations}

## 输出（严格 JSON）
{{"title": "主题标题", "summary": "摘要（100-200字）"}}
"""


async def _summarize_community(G: nx.DiGraph, node_ids: List[str], max_nodes: int) -> Dict:
    sub_nodes = node_ids[:max_nodes]
    entities = [f"{G.nodes[n].get('name','')}({G.nodes[n].get('entity_type','')})" for n in sub_nodes if n in G]
    node_set = set(sub_nodes)
    relations = []
    sub_G = G.subgraph([n for n in sub_nodes if n in G])
    for u, v, data in sub_G.edges(data=True):
        relations.append(f"{G.nodes[u].get('name','')} -[{data.get('relation_type','')}]-> {G.nodes[v].get('name','')}")
        if len(relations) >= 40:
            break
    try:
        res = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是图谱社区摘要器，只返回 JSON。"},
                {"role": "user", "content": SUMMARY_PROMPT.format(
                    entities=json.dumps(entities, ensure_ascii=False),
                    relations=json.dumps(relations, ensure_ascii=False),
                )},
            ],
            complexity=COMPLEXITY_NORMAL,
        )
        return {"title": res.get("title", ""), "summary": res.get("summary", "")}
    except Exception as e:
        logger.warning(f"[社区] 摘要生成失败: {e}")
        # 回退：用实体列表拼一个粗略摘要
        return {"title": "、".join(entities[:3]), "summary": "包含实体：" + "、".join(entities[:15])}


def build_communities(project_id: str, min_size: int = 3) -> Dict:
    """同步入口：构建社区并生成摘要（内部用事件循环跑 LLM 摘要）。

    兼容在已有事件循环（如 FastAPI 异步端点）中调用：检测到运行中的
    事件循环时，转到独立线程中运行，避免 asyncio.run 冲突。
    """
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(build_communities_async(project_id, min_size=min_size))

    # 处于运行中的事件循环：在独立线程跑一个新的事件循环
    import threading

    result_holder: Dict = {}

    def _runner():
        result_holder["result"] = asyncio.run(build_communities_async(project_id, min_size=min_size))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    return result_holder.get("result", {})


async def build_communities_async(project_id: str, min_size: int = 3) -> Dict:
    config = load_config()
    max_nodes = int(getattr(config, "community_max_summary_nodes", 30))

    G = get_nx_graph(project_id)
    if G.number_of_nodes() == 0:
        return {"error": "图谱为空，无法构建社区"}

    raw = _detect_communities(G)
    communities = [c for c in raw if len(c) >= min_size]
    communities.sort(key=len, reverse=True)

    results = []
    for i, node_ids in enumerate(communities):
        summary = await _summarize_community(G, node_ids, max_nodes)
        results.append({
            "id": i,
            "size": len(node_ids),
            "node_ids": node_ids[:max_nodes],
            "title": summary.get("title", ""),
            "summary": summary.get("summary", ""),
        })

    with open(_communities_file(project_id), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info(f"[社区] 构建完成: {len(results)} 个社区（>= {min_size} 节点）")
    return {"communities": len(results), "total_detected": len(raw)}
