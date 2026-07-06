"""下游 RAG 效用反馈（AutoGraph-R1 探索性实现）。

AutoGraph-R1 的核心思想是「以下游 RAG 任务效用为信号优化图谱构建」。
作为探索性落地，本模块：
1. 从原文采样自动生成问题；
2. 用当前图谱（Graph RAG）回答；
3. LLM 评判每个回答是否被图谱有效支撑（效用打分）；
4. 统计哪些实体/关系类型在「有效回答」中被高频使用、哪些 Schema 类型几乎未被利用；
5. 产出效用分与「抽取策略优化建议」，写入 downstream_feedback.json 供迭代参考。
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from config import get_project_dir
from services.graph_store import load_published_graph, _load_schema_dict
from services.chunk_store import get_chunks_by_ids
from services.graph_rag import build_context_prompt
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)


QUESTION_PROMPT = """请基于下面的文本，生成 {n} 个可以用知识图谱回答的问题（关注实体、关系、多跳推理）。

## 文本
{text}

## 输出（严格 JSON）
{{"questions": ["问题1", "问题2"]}}
"""

JUDGE_PROMPT = """下面是一个问题、依据图谱检索到的上下文，以及模型给出的回答。
请判断该回答是否**主要由图谱上下文支撑**（即图谱是否对回答有效）。

## 问题
{question}

## 图谱上下文（三元组/原文）
{context}

## 回答
{answer}

## 输出（严格 JSON）
{{"useful": true 或 false, "reason": "简述"}}
"""


def _feedback_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "downstream_feedback.json"


async def evaluate_graph_utility(project_id: str, sample_size: int = 5, retrieval_mode: str = "graph_flow") -> Dict:
    """评估图谱对下游 RAG 的效用并给出优化建议。"""
    graph = load_published_graph(project_id)
    if not graph.nodes:
        return {"error": "已发布图谱为空，无法评估下游效用"}

    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    schema_entity_types = {et["name"] for et in schema.get("entity_types", [])}

    # 采样有溯源的 chunk 生成问题
    chunk_ids = []
    for n in graph.nodes:
        for c in (n.source_chunk_ids or []):
            if c and c != "cold_start":
                chunk_ids.append(c)
    chunk_ids = list(dict.fromkeys(chunk_ids))
    if not chunk_ids:
        return {"error": "图谱无可溯源片段"}

    if len(chunk_ids) > sample_size:
        step = len(chunk_ids) / sample_size
        sample_ids = [chunk_ids[int(i * step)] for i in range(sample_size)]
    else:
        sample_ids = chunk_ids

    chunks = get_chunks_by_ids(project_id, sample_ids)

    total_q = 0
    useful_q = 0
    type_usage = Counter()      # 有效回答中被使用的实体类型
    details = []

    for c in chunks:
        text = c.get("content", "")
        if not text:
            continue
        try:
            q_res = await llm_gateway.chat_json(
                messages=[
                    {"role": "system", "content": "你是问题生成器，只返回 JSON。"},
                    {"role": "user", "content": QUESTION_PROMPT.format(text=text[:1500], n=2)},
                ],
                complexity=COMPLEXITY_NORMAL,
            )
            questions = [q for q in q_res.get("questions", []) if q][:2]
        except Exception as e:
            logger.warning(f"[下游效用] 问题生成失败: {e}")
            continue

        for q in questions:
            try:
                messages, _, recall_info = await build_context_prompt(
                    project_id, q, max_degree=2, max_start_entities=5, retrieval_mode=retrieval_mode
                )
                answer = ""
                async for piece in llm_gateway.chat_stream(messages, complexity=COMPLEXITY_NORMAL):
                    answer += piece

                context_repr = json.dumps(recall_info.get("edges", []), ensure_ascii=False)
                judge = await llm_gateway.chat_json(
                    messages=[
                        {"role": "system", "content": "你是图谱效用评判器，只返回 JSON。"},
                        {"role": "user", "content": JUDGE_PROMPT.format(
                            question=q, context=context_repr[:2000], answer=answer[:1500]
                        )},
                    ],
                    complexity=COMPLEXITY_NORMAL,
                )
                total_q += 1
                useful = bool(judge.get("useful"))
                if useful:
                    useful_q += 1
                    for node in recall_info.get("nodes", []):
                        if node.get("type"):
                            type_usage[node["type"]] += 1
                details.append({"question": q, "useful": useful, "reason": judge.get("reason", "")})
            except Exception as e:
                logger.warning(f"[下游效用] 问答评估失败: {e}")

    utility = (useful_q / total_q) if total_q else 0.0

    # 未被有效利用的 Schema 实体类型
    underused = sorted(schema_entity_types - set(type_usage.keys()))
    suggestions = []
    if underused:
        suggestions.append(
            f"以下 Schema 实体类型在下游问答中几乎未被利用，可考虑加强其抽取或检查定义是否过窄：{', '.join(underused)}"
        )
    if utility < 0.5 and total_q:
        suggestions.append("图谱下游效用偏低：建议提升关系抽取召回、增加跨片段关系推断或引入社区摘要支持全局问答。")
    if not suggestions:
        suggestions.append("图谱下游效用良好，无明显优化项。")

    report = {
        "benchmark": "downstream_utility(AutoGraph-R1 style)",
        "evaluated_at": datetime.now().isoformat(),
        "retrieval_mode": retrieval_mode,
        "total_questions": total_q,
        "useful_answers": useful_q,
        "utility_score": round(utility, 4),
        "entity_type_usage": dict(type_usage),
        "underused_entity_types": underused,
        "suggestions": suggestions,
        "details": details,
    }

    try:
        with open(_feedback_file(project_id), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[下游效用] 写入反馈文件失败: {e}")

    return report
