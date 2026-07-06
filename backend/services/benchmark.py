"""抽取质量回归基线（MINE-1 + LLM-KG-Bench FactExtract）。

- MINE-1（信息保留率）：从源文本生成原子事实，判断这些事实是否被
  构建出的图谱三元组所覆盖，得到保留率，作为构建质量的回归指标。
- FactExtract（F1）：将抽取出的三元组与 gold 三元组对比，计算 P/R/F1。

参考：KGGen / MINE (NeurIPS 2025)、LLM-KG-Bench 3.0 (ESWC 2025)。
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

from config import get_project_dir
from services.graph_store import load_published_graph, load_draft_graph
from services.chunk_store import get_chunks_by_ids
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)


FACTS_PROMPT = """请从下面这段文本中抽取**原子事实**（每条只表达一个最小的事实陈述），
用于评测知识图谱是否保留了文本中的信息。最多 {max_facts} 条。

## 文本
{text}

## 输出（严格 JSON）
{{"facts": ["事实1", "事实2"]}}
"""

JUDGE_PROMPT = """下面有一组「原子事实」和一组「知识图谱三元组」。
请判断每条事实是否能由这些三元组支撑（信息是否被图谱保留）。

## 原子事实
{facts}

## 知识图谱三元组
{triples}

## 输出（严格 JSON）
{{"results": [{{"fact": "事实", "supported": true 或 false}}]}}
"""


def _graph_triples_for_chunk(graph, chunk_id: str) -> List[str]:
    """收集与某个 chunk 相关的三元组文本。"""
    node_by_id = {n.id: n for n in graph.nodes}
    triples = []
    for e in graph.edges:
        if chunk_id in (e.source_chunk_ids or []):
            s = node_by_id.get(e.source_id)
            t = node_by_id.get(e.target_id)
            if s and t:
                triples.append(f"({s.name}) -[{e.relation_type}]-> ({t.name})")
    # 补充该 chunk 的实体（无关系时也算保留实体信息）
    for n in graph.nodes:
        if chunk_id in (n.source_chunk_ids or []):
            triples.append(f"实体: {n.name} ({n.entity_type})")
    return triples


async def run_mine1(project_id: str, sample_size: int = 10, max_facts_per_chunk: int = 8, status: str = "published") -> Dict:
    """MINE-1 信息保留率评测。"""
    graph = load_published_graph(project_id) if status == "published" else load_draft_graph(project_id)
    if not graph.nodes:
        return {"error": "图谱为空，无法评测"}

    # 选取有图谱关联的 chunk 作为样本
    chunk_ids = set()
    for n in graph.nodes:
        for c in (n.source_chunk_ids or []):
            if c and c != "cold_start":
                chunk_ids.add(c)
    chunk_ids = list(chunk_ids)
    if not chunk_ids:
        return {"error": "图谱无可溯源片段，无法评测"}

    # 均匀采样
    if len(chunk_ids) > sample_size:
        step = len(chunk_ids) / sample_size
        sample_ids = [chunk_ids[int(i * step)] for i in range(sample_size)]
    else:
        sample_ids = chunk_ids

    chunks = get_chunks_by_ids(project_id, sample_ids)
    chunk_text_by_id = {c["chunk_id"]: c.get("content", "") for c in chunks}

    total_facts = 0
    supported_facts = 0
    per_chunk = []

    for cid in sample_ids:
        text = chunk_text_by_id.get(cid, "")
        if not text:
            continue
        try:
            facts_res = await llm_gateway.chat_json(
                messages=[
                    {"role": "system", "content": "你是严谨的事实抽取器，只返回 JSON。"},
                    {"role": "user", "content": FACTS_PROMPT.format(text=text[:2000], max_facts=max_facts_per_chunk)},
                ],
                complexity=COMPLEXITY_NORMAL,
            )
            facts = [f for f in facts_res.get("facts", []) if f]
            if not facts:
                continue
            triples = _graph_triples_for_chunk(graph, cid)
            judge = await llm_gateway.chat_json(
                messages=[
                    {"role": "system", "content": "你是严谨的图谱信息保留评测器，只返回 JSON。"},
                    {"role": "user", "content": JUDGE_PROMPT.format(
                        facts=json.dumps(facts, ensure_ascii=False, indent=2),
                        triples=json.dumps(triples, ensure_ascii=False, indent=2),
                    )},
                ],
                complexity=COMPLEXITY_NORMAL,
            )
            results = judge.get("results", [])
            n_total = len(facts)
            n_sup = sum(1 for r in results if r.get("supported"))
            total_facts += n_total
            supported_facts += n_sup
            per_chunk.append({"chunk_id": cid, "facts": n_total, "supported": n_sup})
        except Exception as e:
            logger.warning(f"[MINE-1] chunk {cid} 评测失败: {e}")

    retention = (supported_facts / total_facts) if total_facts else 0.0
    return {
        "benchmark": "MINE-1",
        "sampled_chunks": len(per_chunk),
        "total_facts": total_facts,
        "supported_facts": supported_facts,
        "retention_rate": round(retention, 4),
        "per_chunk": per_chunk,
    }


def _normalize_triple(s: str, r: str, t: str) -> Tuple[str, str, str]:
    return (s.strip().lower(), r.strip().lower(), t.strip().lower())


def run_factextract(project_id: str, gold_triples: List[Dict], status: str = "published") -> Dict:
    """FactExtract F1：抽取三元组 vs gold 三元组。

    gold_triples 每项形如 {"source": "...", "relation": "...", "target": "..."}（按名称）。
    """
    graph = load_published_graph(project_id) if status == "published" else load_draft_graph(project_id)
    node_by_id = {n.id: n for n in graph.nodes}

    pred_set = set()
    for e in graph.edges:
        s = node_by_id.get(e.source_id)
        t = node_by_id.get(e.target_id)
        if s and t:
            pred_set.add(_normalize_triple(s.name, e.relation_type, t.name))

    gold_set = set()
    for g in gold_triples:
        gold_set.add(_normalize_triple(g.get("source", ""), g.get("relation", ""), g.get("target", "")))

    if not gold_set:
        return {"error": "gold 三元组为空"}

    tp = len(pred_set & gold_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(gold_set) if gold_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "benchmark": "FactExtract",
        "predicted_triples": len(pred_set),
        "gold_triples": len(gold_set),
        "true_positive": tp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
