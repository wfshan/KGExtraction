"""
抽取工作流编排（自研 asyncio 流水线）
主流程：文档分片 → 循环（实体抽取 → 消歧 → 关系推断 → 自我修正 → 更新图谱）

关键不变量：
- 实体身份 = (name, entity_type)，节点 ID 由该复合键确定性派生（跨 run 稳定）；
- 证据短句经确定性验证（逐字命中原文）后带 verified 标记入库；
- 规则校验丢弃的实体/关系写入被拒项存储（可审计、供 Schema 缺口检测）；
- 已有实体在新片段中的出现回写数据库（溯源不丢失）；
- 每个分片处理前检查 token 预算，超限优雅停止。
"""
import json
import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from time import perf_counter
import numpy as np
import traceback

from config import get_project_dir, load_config
from services.extraction_logger import set_run_context, log_extraction
from models.graph import Node, Edge
from services.extraction.entity import extract_entities, disambiguate_entities
from services.extraction.relation import extract_relations, infer_cross_chunk_relations
from services.extraction.combined import extract_entities_and_relations
from services.extraction.correction import self_correct
from services.fast_similarity import rank_entity_candidates, use_vector_similarity
from services.evidence import build_evidence
from services.schema_utils import (
    relation_source_types,
    relation_target_types,
    type_satisfies,
    format_constraint,
)
from services import usage_tracker
from services.graph_store import (
    load_draft_graph,
    get_draft_entity_map,
    add_nodes_to_draft,
    add_edges_to_draft,
    add_chunk_links_to_draft_nodes,
    entity_key,
)

logger = logging.getLogger(__name__)

# 保底实体类型：未抽到任何本体实体的片段挂载到该类型节点上（文档结构层）
FALLBACK_ENTITY_TYPE = "未归类片段"
# 文档结构：每个片段一个锚点节点，用该关系表示“下一段”（文档结构层）
SEGMENT_ENTITY_TYPE = "文档片段"
NEXT_SEGMENT_RELATION = "下一段"


def _register_node(entity_map: Dict[tuple, Node], name_index: Dict[str, List[Node]], node: Node):
    """同时维护复合键实体表与 name → nodes 二级索引。"""
    key = entity_key(node.name, node.entity_type)
    entity_map[key] = node
    bucket = name_index.setdefault(node.name, [])
    if node not in bucket:
        bucket.append(node)


def _build_name_index(entity_map: Dict[tuple, Node]) -> Dict[str, List[Node]]:
    idx: Dict[str, List[Node]] = defaultdict(list)
    for node in entity_map.values():
        idx[node.name].append(node)
    return dict(idx)


def _resolve_node_by_name(
    name: str,
    name_index: Dict[str, List[Node]],
    preferred_types: Optional[Set[str]] = None,
    chunk_types: Optional[Dict[str, str]] = None,
) -> Optional[Node]:
    """按名称消解实体引用。

    同名多类型时的消解顺序：当前片段抽出的类型 > 关系约束允许的类型 > 唯一候选。
    无法唯一消解时返回 None（宁可拒绝也不误连）。
    """
    candidates = name_index.get(name, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # 优先：当前片段中该名称对应的类型
    if chunk_types and name in chunk_types:
        for c in candidates:
            if c.entity_type == chunk_types[name]:
                return c
    # 其次：满足关系约束的候选（唯一时才采用）
    if preferred_types:
        matching = [c for c in candidates if c.entity_type in preferred_types]
        if len(matching) == 1:
            return matching[0]
    return None


async def run_extraction_pipeline_sync(
    project_id: str,
    run_id: str,
    progress_callback: Optional[Callable] = None,
    initial_stats: Optional[Dict] = None,
    skip_chunks: int = 0,
    only_doc_ids: Optional[List[str]] = None,
    plan=None,
):
    """
    执行完整的抽取工作流（异步协程并发）。

    only_doc_ids 非空时仅处理这些文档的分片（增量摄入 / delta merge），
    新抽取的实体会与既有草稿图谱按 (name, entity_type) 合并，不重建全图。

    plan（v3）：抽取计划。第①步中执行器把 Plan 解析回等价的 config 覆盖参数，
    使执行路径「经过 Plan」但外部行为与直接读 config 完全一致（round-trip 保证）。
    真正的「按 Plan 遍历原语执行」在第②步落地。
    """
    project_dir = get_project_dir(project_id)
    config = load_config()

    # v3：若提供 Plan，用其解析出的参数覆盖 config（不改动下方任何逐项逻辑）
    if plan is not None:
        try:
            from services.planning import plan_to_execution_params
            overrides = plan_to_execution_params(plan)
            if overrides:
                config = config.model_copy(update=overrides)
                print(f"[Pipeline] 已按 Plan {getattr(plan, 'plan_id', '?')} 覆盖抽取参数: {overrides}")
        except Exception as e:
            print(f"[Pipeline] 解析 Plan 参数失败，回退 config: {e}")

    # 清除可能导致连接问题的代理环境变量
    for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                "all_proxy", "ALL_PROXY"]:
        os.environ.pop(var, None)

    print(f"[Pipeline] 加载 Schema...")
    schema_file = project_dir / "schema.json"
    with open(schema_file, "r", encoding="utf-8") as f:
        master_schema = json.load(f)

    # 预加载所有文档配置，用于获取专属抽取目标
    doc_configs = {}
    docs_file = project_dir / "documents.json"
    if docs_file.exists():
        with open(docs_file, "r", encoding="utf-8") as f:
            docs = json.load(f)
            for d in docs:
                doc_configs[d["id"]] = {
                    "target_entities": d.get("target_entities", []),
                    "target_relations": d.get("target_relations", [])
                }

    # 反思案例库（OneKE）：将人工复核经验作为 few-shot 指引注入抽取 prompt
    reflection_hint = ""
    try:
        from services.reflection import format_cases_for_prompt
        reflection_hint = format_cases_for_prompt(project_id)
        if reflection_hint:
            print(f"[Pipeline] 已加载反思案例指引 ({len(reflection_hint)} 字符)")
    except Exception as e:
        print(f"[Pipeline] 加载反思案例失败: {e}")

    all_chunks = _load_all_chunks(project_dir)
    if only_doc_ids:
        only_set = set(only_doc_ids)
        all_chunks = [c for c in all_chunks if c.get("doc_id") in only_set]
        print(f"[Pipeline] 增量模式：仅处理文档 {only_doc_ids} 的 {len(all_chunks)} 个分片")
    total_chunks = len(all_chunks)

    if total_chunks == 0:
        raise ValueError("没有可处理的具体文档分片")

    # 配置独立执行上下文（让 Logger 能获取正确的日志路径）
    set_run_context(project_id, run_id)
    log_extraction(f"=== 开始抽取任务 {run_id} === (共 {total_chunks} 个分片)")

    token_budget = int(getattr(config, "run_token_budget", 0) or 0)
    if token_budget > 0:
        log_extraction(f"本次任务 token 预算上限: {token_budget}")

    print(f"[Pipeline] 共 {total_chunks} 个分片")
    _report(progress_callback, progress=5.0, current_step="初始化完成，准备处理分片",
            stats={"total_chunks": total_chunks})

    # 向量存储（可选）
    vector_store = None
    embed_fn = None
    if use_vector_similarity(config):
        try:
            from services.vector_store import VectorStore
            from services.embedding import embed_text
            vector_store = VectorStore(project_dir, name="entity")
            embed_fn = embed_text
            print(f"[Pipeline] 实体分项向量索引初始化成功: {vector_store.index_name}")
        except Exception as e:
            print(f"[Pipeline] 向量存储不可用 (跳过消歧): {e}")
    else:
        print("[Pipeline] 当前为快速相似度模式，跳过实体向量索引初始化")

    # 获取全图所有草稿实体（(name, entity_type) 复合键）+ name 二级索引
    entity_map = await get_draft_entity_map(project_id)
    name_index = _build_name_index(entity_map)

    # 将已有实体加入向量存储（批量处理）
    if vector_store and embed_fn and entity_map:
        print(f"[Pipeline] 将 {len(entity_map)} 个已有实体加入向量索引...")
        try:
            from services.embedding import embed_texts
            nodes_list = list(entity_map.values())
            batch_size = 500
            for i in range(0, len(nodes_list), batch_size):
                batch_nodes = nodes_list[i:i + batch_size]
                texts = [node.name for node in batch_nodes]
                vecs = embed_texts(texts)
                metas = [{"node_id": node.id, "name": node.name, "entity_type": node.entity_type} for node in batch_nodes]
                vector_store.add(vecs, metas)
        except Exception as e:
            print(f"[Pipeline] 批量导入实体向量失败: {e}")

    all_stats = initial_stats or {
        "total_chunks": total_chunks,
        "processed_chunks": 0,
        "entities_extracted": 0,
        "relations_extracted": 0,
        "entities_deduplicated": 0,
        "entities_rejected": 0,
        "relations_rejected": 0,
        "tokens_used": 0,
        "timing_extract_ms": 0.0,
        "timing_disambiguation_ms": 0.0,
        "timing_relations_ms": 0.0,
        "timing_self_correction_ms": 0.0,
        "timing_total_chunk_ms": 0.0,
    }
    all_stats["total_chunks"] = total_chunks
    for k in ("timing_extract_ms", "timing_disambiguation_ms", "timing_relations_ms",
              "timing_self_correction_ms", "timing_total_chunk_ms",
              "entities_rejected", "relations_rejected"):
        all_stats.setdefault(k, 0.0 if k.startswith("timing") else 0)

    orphan_chunk_ids = set()
    mem_lock = asyncio.Lock()
    budget_stopped = asyncio.Event()

    # 批量缓冲区
    node_buffer = []
    edge_buffer = []
    chunk_link_buffer = []   # 已有实体在新片段的出现：回写溯源
    rejected_buffer = []     # 规则校验丢弃项：落库供审计与 Schema 缺口检测

    async def flush_buffer():
        nonlocal node_buffer, edge_buffer, chunk_link_buffer, rejected_buffer
        if node_buffer:
            await add_nodes_to_draft(project_id, node_buffer)
            node_buffer = []
        if edge_buffer:
            await add_edges_to_draft(project_id, edge_buffer)
            edge_buffer = []
        if chunk_link_buffer:
            await add_chunk_links_to_draft_nodes(project_id, chunk_link_buffer)
            chunk_link_buffer = []
        if rejected_buffer:
            from services.rejected_store import add_rejected
            pending_rejected = rejected_buffer
            rejected_buffer = []
            await asyncio.to_thread(add_rejected, project_id, pending_rejected)

    def _buffer_rejected(chunk_id: str, kind: str, name: str, item_type: str, reason: str, payload: Dict):
        rejected_buffer.append({
            "run_id": run_id,
            "chunk_id": chunk_id,
            "kind": kind,
            "name": name,
            "item_type": item_type,
            "reason": reason,
            "payload": payload,
        })

    # inductive 类型集合（供证据分道：命中已有 inductive 实体时用 span 模式）
    inductive_type_names = set()
    if plan is not None:
        inductive_type_names = {
            n for n, kt in plan.knowledge_types.items()
            if str(getattr(kt.abstractness, "value", kt.abstractness)) == "inductive"
        }

    def _link_existing_node(node: Node, chunk_id: str, raw_evidence=None):
        """已有实体在当前片段出现：更新内存并缓冲数据库回写（修复溯源丢失）。"""
        if chunk_id not in node.source_chunk_ids:
            node.source_chunk_ids.append(chunk_id)
        ev = []
        if raw_evidence:
            ev_mode = "span" if node.entity_type in inductive_type_names else "verbatim"
            ev = build_evidence(chunk_id, raw_evidence, _chunk_text_by_id.get(chunk_id, ""), config.enable_evidence_anchor, ev_mode)
        chunk_link_buffer.append({
            "node_id": node.id,
            "chunk_ids": [chunk_id],
            "evidence_quotes": ev,
        })

    # 证据验证需要在缓冲回写处访问 chunk 原文
    _chunk_text_by_id = {c["id"]: c.get("text", "") for c in all_chunks}

    async def process_chunk(chunk_index: int, chunk: dict):
        set_run_context(project_id, run_id)
        chunk_text = chunk["text"]
        chunk_id = chunk["id"]

        # token 预算门：超限后不再发起新的分片处理
        if budget_stopped.is_set():
            return
        if token_budget > 0 and usage_tracker.budget_exceeded(project_id, run_id, token_budget):
            if not budget_stopped.is_set():
                budget_stopped.set()
                log_extraction(f"token 预算已用尽（上限 {token_budget}），停止处理剩余分片", "WARNING")
            return

        async with mem_lock:
            current_processed = all_stats["processed_chunks"]
            progress = 5.0 + (current_processed / total_chunks) * 85.0
            print(f"[Pipeline] === 片段 {chunk_index + 1}/{total_chunks} ===")
            log_extraction(f"开始处理片段 {chunk_index + 1}/{total_chunks} [{chunk_id}]")
            _report(progress_callback, progress=progress,
                    current_step=f"处理片段 {current_processed}/{total_chunks}",
                    stats=all_stats)

        try:
            chunk_begin = perf_counter()
            llm_stream_log = bool(getattr(config, "llm_stream_log", False))
            doc_id_for_config = chunk.get("doc_id", "")
            doc_config = doc_configs.get(doc_id_for_config, {})
            chunk_schema = _build_chunk_schema(master_schema, doc_config)
            has_target_filter = bool(doc_config.get("target_entities")) or bool(doc_config.get("target_relations"))

            t_extract_begin = perf_counter()
            # v3：候选生成按知识类型抽象度分派（surface→现有抽取，inductive→归纳）。
            # 无 inductive 类型时等价于现状 one-pass/multi-pass 抽取（零回归）。
            log_extraction(f"[片段 {chunk_id}] 候选生成（按 Plan 抽象度分派）...")
            from services.extraction.dispatch import generate_candidates
            cand = await generate_candidates(
                chunk_text, chunk_schema, plan, config,
                reflection_hint=reflection_hint, stream_log=llm_stream_log,
            )
            raw_entities = cand["entities"]
            raw_relations = cand["relations"]
            # 归纳忠实度未通过的候选落被拒项（挡幻觉归纳）
            if cand.get("rejected_inductive"):
                async with mem_lock:
                    all_stats["entities_rejected"] += len(cand["rejected_inductive"])
                    for rej in cand["rejected_inductive"]:
                        _buffer_rejected(chunk_id, "entity", rej["name"], rej["entity_type"],
                                         rej["reason"], rej.get("payload", {}))
            t_extract_ms = (perf_counter() - t_extract_begin) * 1000
            async with mem_lock:
                all_stats["timing_extract_ms"] += t_extract_ms

            # --- 严格实体校验（被拒项落库，不静默丢弃） ---
            raw_entities, rejected_entities = _validate_entities_strict(raw_entities, chunk_schema)
            if rejected_entities:
                async with mem_lock:
                    all_stats["entities_rejected"] += len(rejected_entities)
                    for rej in rejected_entities:
                        _buffer_rejected(
                            chunk_id, "entity",
                            rej.get("name", ""), rej.get("entity_type", ""),
                            rej.get("_reject_reason", "entity_type_not_in_schema"),
                            {k: v for k, v in rej.items() if not k.startswith("_")},
                        )
            log_extraction(f"[片段 {chunk_id}] 经校验后保留 {len(raw_entities)} 个有效实体（拒绝 {len(rejected_entities)} 个）")

            new_nodes = []
            async with mem_lock:
                all_stats["entities_extracted"] += len(raw_entities)

            # 当前片段内 name → entity_type 映射，供关系消解使用
            chunk_types: Dict[str, str] = {}

            # 先做名称去重与已有实体命中，再做批量消歧，减少 LLM 次数
            pending_entities = []
            disambiguation_entities = []
            seen_keys_in_chunk = set()
            disambiguation_low_conf_only = bool(getattr(config, "disambiguation_low_confidence_only", False))
            disambiguation_conf_threshold = float(getattr(config, "disambiguation_entity_confidence_threshold", 0.85))
            async with mem_lock:
                for entity_data in raw_entities:
                    entity_name = entity_data.get("name", "")
                    entity_type = entity_data.get("entity_type", "")
                    if not entity_name:
                        continue
                    ekey = entity_key(entity_name, entity_type)
                    chunk_types.setdefault(entity_name, entity_type)

                    # 同片段同名同类型实体直接去重
                    if ekey in seen_keys_in_chunk:
                        all_stats["entities_deduplicated"] += 1
                        continue
                    seen_keys_in_chunk.add(ekey)

                    # 先与全局实体表做快速命中（(name, type) 复合键）
                    if ekey in entity_map:
                        _link_existing_node(entity_map[ekey], chunk_id, entity_data.get("evidence"))
                        all_stats["entities_deduplicated"] += 1
                        continue

                    pending_entities.append(entity_data)
                    entity_conf = float(entity_data.get("confidence", 1.0))
                    if (not disambiguation_low_conf_only) or (entity_conf < disambiguation_conf_threshold):
                        disambiguation_entities.append(entity_data)

            decisions_by_name = {}
            if disambiguation_entities and config.enable_disambiguation:
                try:
                    t_disam_begin = perf_counter()
                    all_candidates = []
                    direct_match_by_name = {}
                    fast_path_score = float(getattr(config, "disambiguation_fast_path_score", 0.92))
                    candidate_limit = max(1, int(getattr(config, "disambiguation_candidate_limit_per_entity", 8)))
                    search_results = []

                    if use_vector_similarity(config) and vector_store and embed_fn:
                        from services.embedding import embed_texts

                        pending_names = [e.get("name", "") for e in disambiguation_entities]
                        vecs = await asyncio.to_thread(embed_texts, pending_names)
                        search_tasks = [
                            asyncio.to_thread(
                                vector_store.search,
                                vecs[idx],
                                top_k=config.vector_top_k,
                                score_threshold=config.score_threshold
                            )
                            for idx in range(len(disambiguation_entities))
                        ]
                        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
                    else:
                        async with mem_lock:
                            entity_candidates = [
                                {"node_id": n.id, "name": n.name, "entity_type": n.entity_type}
                                for n in entity_map.values()
                            ]
                        search_tasks = []
                        for entity_data in disambiguation_entities:
                            entity_name = entity_data.get("name", "")
                            entity_type = entity_data.get("entity_type", "")
                            search_tasks.append(
                                asyncio.to_thread(
                                    rank_entity_candidates,
                                    entity_name,
                                    entity_candidates,
                                    config.vector_top_k,
                                    float(getattr(config, "fast_score_threshold", 0.25)),
                                    entity_type,
                                )
                            )
                        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

                    for idx, entity_data in enumerate(disambiguation_entities):
                        entity_name = entity_data.get("name", "")
                        if not entity_name:
                            continue

                        raw_candidates = search_results[idx]
                        if isinstance(raw_candidates, Exception):
                            continue

                        # 优先保留同类型候选，减少误判与 prompt 噪声
                        typed_candidates = [
                            (meta, score)
                            for meta, score in raw_candidates
                            if meta.get("entity_type") == entity_data.get("entity_type")
                        ]
                        candidates = (typed_candidates or raw_candidates)[:candidate_limit]

                        if candidates:
                            top_meta, top_score = candidates[0]
                            if (
                                top_score >= fast_path_score
                                and top_meta.get("entity_type") == entity_data.get("entity_type")
                            ):
                                direct_match_by_name[entity_name] = {
                                    "new_entity_name": entity_name,
                                    "match_entity_id": top_meta.get("node_id"),
                                    "is_same": True,
                                    "reason": "high_score_fast_path",
                                }
                            all_candidates.extend(candidates)

                    if all_candidates and len(direct_match_by_name) < len(disambiguation_entities):
                        # 候选按 node_id 去重，保留最高分，控制 prompt 规模
                        dedup_candidates = {}
                        for meta, score in all_candidates:
                            node_id = meta.get("node_id")
                            if not node_id:
                                continue
                            prev = dedup_candidates.get(node_id)
                            if prev is None or score > prev[1]:
                                dedup_candidates[node_id] = (meta, score)

                        merged_candidates = list(dedup_candidates.values())[: config.vector_top_k * 3]
                        decisions = await disambiguate_entities(
                            [e for e in disambiguation_entities if e.get("name", "") not in direct_match_by_name],
                            merged_candidates,
                            chunk_text,
                            stream_log=llm_stream_log
                        )
                        decisions_by_name = {
                            d.get("new_entity_name", ""): d for d in decisions if d.get("new_entity_name")
                        }
                    decisions_by_name.update(direct_match_by_name)
                    t_disam_ms = (perf_counter() - t_disam_begin) * 1000
                    async with mem_lock:
                        all_stats["timing_disambiguation_ms"] += t_disam_ms
                except Exception as e:
                    log_extraction(f"[片段 {chunk_id}] 批量消歧失败: {e}", "WARNING")

            # 将 pending_entities 落库：先尝试按消歧结果归并，再创建新节点
            async with mem_lock:
                entity_id_map = {node.id: node for node in entity_map.values()}

            for entity_data in pending_entities:
                entity_name = entity_data.get("name", "")
                entity_type = entity_data.get("entity_type", "")
                if not entity_name:
                    continue

                decision = decisions_by_name.get(entity_name)
                if decision and decision.get("is_same") and decision.get("match_entity_id"):
                    match_node = entity_id_map.get(decision["match_entity_id"])
                    if match_node:
                        async with mem_lock:
                            # 并发下再次确认
                            current_match_node = entity_map.get(entity_key(match_node.name, match_node.entity_type))
                            if current_match_node:
                                if chunk_id not in current_match_node.source_chunk_ids:
                                    _link_existing_node(current_match_node, chunk_id, entity_data.get("evidence"))
                                # 消歧归并：当前片段中该名称指向已有实体
                                chunk_types[entity_name] = current_match_node.entity_type
                                all_stats["entities_deduplicated"] += 1
                        continue

                async with mem_lock:
                    # 并发下二次检查，避免重复写入
                    ekey = entity_key(entity_name, entity_type)
                    if ekey in entity_map:
                        _link_existing_node(entity_map[ekey], chunk_id, entity_data.get("evidence"))
                        all_stats["entities_deduplicated"] += 1
                        continue

                    # 证据分道：inductive 知识用 span 模式（不逐字校验，已过忠实度）
                    ev_mode = "span" if entity_data.get("_abstractness") == "inductive" else "verbatim"
                    node = Node(
                        name=entity_name,
                        entity_type=entity_type,
                        properties=entity_data.get("properties", {}),
                        source_chunk_ids=[chunk_id],
                        evidence_quotes=build_evidence(chunk_id, entity_data.get("evidence"), chunk_text, config.enable_evidence_anchor, ev_mode),
                        confidence=entity_data.get("confidence", 1.0),
                        run_id=run_id,
                    )
                    _register_node(entity_map, name_index, node)
                    new_nodes.append(node)



            if new_nodes:
                async with mem_lock:
                    node_buffer.extend(new_nodes)
                    if len(node_buffer) >= config.database_batch_size:
                        await flush_buffer()
                log_extraction(f"[片段 {chunk_id}] 缓冲了 {len(new_nodes)} 个节点")

                if vector_store:
                    try:
                        from services.embedding import embed_texts
                        texts = [n.name for n in new_nodes]
                        vecs = await asyncio.to_thread(embed_texts, texts)
                        metas = [{"node_id": n.id, "name": n.name, "entity_type": n.entity_type} for n in new_nodes]
                        await asyncio.to_thread(vector_store.add, vecs, metas)
                    except Exception as e:
                        log_extraction(f"[片段 {chunk_id}] 批量添加向量失败: {e}", "WARNING")

            chunk_entity_list = []
            async with mem_lock:
                for e in raw_entities:
                    e_name = e.get("name", "")
                    resolved_type = chunk_types.get(e_name, e.get("entity_type", ""))
                    if entity_key(e_name, resolved_type) in entity_map:
                        chunk_entity_list.append({"name": e_name, "entity_type": resolved_type})

            if config.extraction_mode != "one-pass":
                log_extraction(f"[片段 {chunk_id}] 关系抽取中...")
                t_rel_begin = perf_counter()
                raw_relations = await extract_relations(
                    chunk_text,
                    chunk_entity_list,
                    chunk_schema,
                    stream_log=llm_stream_log,
                    extra_guidance=reflection_hint,
                )
                t_rel_ms = (perf_counter() - t_rel_begin) * 1000
                async with mem_lock:
                    all_stats["timing_relations_ms"] += t_rel_ms

            if config.enable_cross_chunk_inference:
                global_sample = []
                async with mem_lock:
                    if entity_map and len(entity_map) > len(chunk_entity_list):
                        chunk_names = {e["name"] for e in chunk_entity_list}
                        global_sample = [
                            {"name": n.name, "entity_type": n.entity_type}
                            for n in entity_map.values() if n.name not in chunk_names
                        ]
                if global_sample:
                    # 候选按与当前片段的相关性排序（名称命中原文优先），而非任取前 N 个
                    candidate_limit = max(1, int(getattr(config, "cross_chunk_candidate_limit", 20)))
                    global_sample = _rank_global_candidates(chunk_text, global_sample, candidate_limit)
                if global_sample:
                    try:
                        t_rel_begin = perf_counter()
                        cross_rels = await infer_cross_chunk_relations(
                            chunk_text,
                            chunk_entity_list,
                            global_sample,
                            chunk_schema,
                            stream_log=llm_stream_log,
                        )
                        # 该步骤调用大模型，计入关系阶段耗时统计
                        t_rel_ms = (perf_counter() - t_rel_begin) * 1000
                        async with mem_lock:
                            all_stats["timing_relations_ms"] += t_rel_ms
                        raw_relations.extend(cross_rels)
                    except Exception: pass

            # === Schema 驱动的存量关系链接 ===
            # 当用户筛选了目标实体时，新抽取的实体需要与存量图谱中「相关类型」的实体建立关系
            if has_target_filter and new_nodes:
                try:
                    t_rel_begin = perf_counter()
                    schema_link_rels = await _schema_driven_relation_linking(
                        new_nodes, master_schema, entity_map, chunk_text, chunk_schema, mem_lock, llm_stream_log
                    )
                    t_rel_ms = (perf_counter() - t_rel_begin) * 1000
                    async with mem_lock:
                        all_stats["timing_relations_ms"] += t_rel_ms
                    if schema_link_rels:
                        raw_relations.extend(schema_link_rels)
                        log_extraction(f"[片段 {chunk_id}] Schema 驱动链接发现 {len(schema_link_rels)} 条关系")
                except Exception as e:
                    log_extraction(f"[片段 {chunk_id}] Schema 驱动链接失败: {e}", "WARNING")

            # --- 严格关系校验（被拒项落库，实体引用按复合键消解） ---
            async with mem_lock:
                raw_relations, rejected_relations = _validate_relations_strict(
                    raw_relations, chunk_schema, name_index, chunk_types
                )
            if rejected_relations:
                async with mem_lock:
                    all_stats["relations_rejected"] += len(rejected_relations)
                    for rej in rejected_relations:
                        _buffer_rejected(
                            chunk_id, "relation",
                            f"{rej.get('source_name', '')}->{rej.get('target_name', '')}",
                            rej.get("relation_type", ""),
                            rej.get("_reject_reason", "invalid_relation"),
                            {k: v for k, v in rej.items() if not k.startswith("_")},
                        )
            log_extraction(f"[片段 {chunk_id}] 经校验后保留 {len(raw_relations)} 条有效关系（拒绝 {len(rejected_relations)} 条）")

            final_relations = raw_relations
            if config.enable_self_correction:
                try:
                    t_correct_begin = perf_counter()
                    res = await self_correct(
                        chunk_entity_list,
                        raw_relations,
                        chunk_schema,
                        stream_log=llm_stream_log
                    )
                    t_correct_ms = (perf_counter() - t_correct_begin) * 1000
                    async with mem_lock:
                        all_stats["timing_self_correction_ms"] += t_correct_ms
                    async with mem_lock:
                        final_relations, _ = _validate_relations_strict(
                            res.get("relations", []), chunk_schema, name_index, chunk_types
                        )
                except Exception: pass

            new_edges = []
            async with mem_lock:
                for rel in final_relations:
                    s_node = rel.get("_source_node")
                    t_node = rel.get("_target_node")
                    if s_node and t_node:
                        edge = Edge(
                            source_id=s_node.id,
                            target_id=t_node.id,
                            relation_type=rel.get("relation_type", ""),
                            properties=rel.get("properties", {}),
                            source_chunk_ids=[chunk_id],
                            evidence_quotes=build_evidence(chunk_id, rel.get("evidence"), chunk_text, config.enable_evidence_anchor),
                            confidence=rel.get("confidence", 1.0),
                            run_id=run_id,
                        )
                        new_edges.append(edge)

            if new_edges:
                async with mem_lock:
                    edge_buffer.extend(new_edges)
                    if len(edge_buffer) >= config.database_batch_size:
                        await flush_buffer()
                    all_stats["relations_extracted"] += len(new_edges)

            chunk_has_any_node = False
            async with mem_lock:
                for node in entity_map.values():
                    if chunk_id in node.source_chunk_ids:
                        chunk_has_any_node = True
                        break
            if not chunk_has_any_node:
                doc_id = chunk.get("doc_id", "")
                fallback_node = Node(id=chunk_id, name=f"片段-{doc_id[:8]}-{chunk_index}", entity_type=FALLBACK_ENTITY_TYPE, properties={}, source_chunk_ids=[chunk_id], confidence=0.5, run_id=run_id)
                async with mem_lock:
                    _register_node(entity_map, name_index, fallback_node)
                    orphan_chunk_ids.add(chunk_id)
                    node_buffer.append(fallback_node)
                    if len(node_buffer) >= config.database_batch_size:
                        await flush_buffer()

            async with mem_lock:
                all_stats["timing_total_chunk_ms"] += (perf_counter() - chunk_begin) * 1000
                all_stats["processed_chunks"] += 1
                all_stats["tokens_used"] = usage_tracker.get_usage(project_id, run_id)["total_tokens"]
                curr = all_stats["processed_chunks"]
                _report(progress_callback, progress=5.0 + (curr/total_chunks)*85.0, current_step=f"处理片段 {curr}/{total_chunks}", stats=all_stats)

        except Exception as e:
            log_extraction(f"[片段 {chunk_id}] 处理失败: {e}", "ERROR")
            traceback.print_exc()
            async with mem_lock:
                all_stats["timing_total_chunk_ms"] += (perf_counter() - chunk_begin) * 1000
                all_stats["processed_chunks"] += 1

    max_workers = getattr(config, "parallel_processes", 5)
    log_extraction(f"启动并发抽取: 并发数 {max_workers}")
    semaphore = asyncio.Semaphore(max_workers)

    async def sem_task(index, chunk):
        async with semaphore:
            await process_chunk(index, chunk)

    tasks_to_run = list(enumerate(all_chunks))[skip_chunks:]
    await asyncio.gather(*(sem_task(index, chunk) for index, chunk in tasks_to_run))
    await flush_buffer()

    # 文档结构固化：为每个片段建立「文档片段」锚点（非保底片段）并添加「下一段」边。
    # 这些节点/边属于文档结构层，图算法（子图/PPR/社区）默认不参与（见 graph_store 层分离）。
    await _add_document_structure(project_id, all_chunks, orphan_chunk_ids, add_nodes_to_draft, add_edges_to_draft, run_id)

    # 归纳知识治理收尾：先跨案例语义归并（同一知识不同措辞合并、支撑证据累积），
    # 再按归并后的「支撑案例数」客观化可信度，取代 LLM 自报
    try:
        from services.extraction.induction import merge_semantic_inductive, objectify_inductive_confidence
        n_merged = await merge_semantic_inductive(project_id)
        if n_merged:
            log_extraction(f"[归纳治理] 语义归并完成：{n_merged} 条同义归纳知识已合并，支撑证据已累积")
        n_obj = objectify_inductive_confidence(project_id)
        if n_obj:
            log_extraction(f"[归纳治理] 已按支撑案例数客观化 {n_obj} 个归纳知识的可信度")
    except Exception as e:
        log_extraction(f"[归纳治理] 语义归并/可信度客观化失败: {e}", "WARNING")

    # 后验本体批量修正（OAK+MEND），可选
    if getattr(config, "enable_post_correction", False):
        try:
            _report(progress_callback, progress=92.0, current_step="后验本体批量修正中...", stats=all_stats)
            from services.extraction.post_correction import post_extraction_correction
            pc_stats = await post_extraction_correction(project_id)
            all_stats["post_correction"] = pc_stats
            log_extraction(f"[后验修正] {pc_stats}")
        except Exception as e:
            log_extraction(f"[后验修正] 失败: {e}", "WARNING")

    # 最终汇总
    final_usage = usage_tracker.get_usage(project_id, run_id)
    all_stats["tokens_used"] = final_usage["total_tokens"]
    all_stats["llm_calls"] = final_usage["calls"]
    if budget_stopped.is_set():
        all_stats["budget_stopped"] = True

    _report(progress_callback,
            progress=95.0,
            current_step=("token 预算用尽，提前停止" if budget_stopped.is_set() else "抽取完成，生成统计信息"),
            stats=all_stats)

    print(
        f"[Pipeline] ✅ 抽取完成: {all_stats['entities_extracted']} 实体, "
        f"{all_stats['relations_extracted']} 关系, "
        f"{all_stats['entities_deduplicated']} 去重, "
        f"拒绝 {all_stats.get('entities_rejected', 0)}实体/{all_stats.get('relations_rejected', 0)}关系, "
        f"{all_stats['tokens_used']} tokens"
    )


async def _add_document_structure(
    project_id: str,
    all_chunks: List[Dict],
    orphan_chunk_ids: Set[str],
    add_nodes_to_draft: Callable,
    add_edges_to_draft: Callable,
    run_id: str = "",
):
    """
    固化文档结构到图谱（文档结构层）：为每个片段建立顺序锚点（非保底片段用「文档片段」节点），
    并添加「下一段」边。该层与知识层分离，仅用于可视化与结构溯源，不参与图算法。
    """
    by_doc: Dict[str, List[Dict]] = defaultdict(list)
    for c in all_chunks:
        by_doc[c["doc_id"]].append(c)
    for doc_id in by_doc:
        by_doc[doc_id].sort(key=lambda c: c.get("index", 0))

    segment_nodes: List[Node] = []
    edges_next: List[Edge] = []

    for doc_id, chunks in by_doc.items():
        if len(chunks) <= 1:
            continue
        segment_ids: List[str] = []
        for i, chunk in enumerate(chunks):
            chunk_id = chunk["id"]
            if chunk_id in orphan_chunk_ids:
                segment_id = chunk_id
            else:
                segment_id = chunk_id + "_seg"
                segment_nodes.append(
                    Node(
                        id=segment_id,
                        name=f"片段-{doc_id[:8]}-{chunk.get('index', i)}",
                        entity_type=SEGMENT_ENTITY_TYPE,
                        properties={"doc_id": doc_id, "index": chunk.get("index", i)},
                        source_chunk_ids=[chunk_id],
                        confidence=1.0,
                        run_id=run_id,
                    )
                )
            segment_ids.append(segment_id)
        for i in range(len(segment_ids) - 1):
            edges_next.append(
                Edge(
                    source_id=segment_ids[i],
                    target_id=segment_ids[i + 1],
                    relation_type=NEXT_SEGMENT_RELATION,
                    properties={},
                    source_chunk_ids=[],
                    confidence=1.0,
                    run_id=run_id,
                )
            )

    if segment_nodes:
        await add_nodes_to_draft(project_id, segment_nodes)
        log_extraction(f"[文档结构] 已添加 {len(segment_nodes)} 个文档片段锚点节点")
    if edges_next:
        await add_edges_to_draft(project_id, edges_next)
        log_extraction(f"[文档结构] 已添加 {len(edges_next)} 条「下一段」边")


def _load_all_chunks(project_dir: Path) -> List[Dict]:
    """加载项目所有文档的分片"""
    chunks_dir = project_dir / "chunks"
    all_chunks = []
    if chunks_dir.exists():
        for chunk_file in sorted(chunks_dir.glob("*_chunks.json")):
            with open(chunk_file, "r", encoding="utf-8") as f:
                chunks = json.load(f)
                all_chunks.extend(chunks)
    return all_chunks

def _build_chunk_schema(master_schema: Dict, doc_config: Dict) -> Dict:
    """根据文档专属配置裁剪 Schema（只保留选中的实体类型，并自动推导相关关系）"""
    target_entities = set(doc_config.get("target_entities", []))
    target_relations = set(doc_config.get("target_relations", []))

    # 如果没有配置目标，则使用全量 Schema
    if not target_entities and not target_relations:
        return master_schema

    filtered_schema = {
        "entity_types": master_schema.get("entity_types", []),
        "relation_types": master_schema.get("relation_types", [])
    }

    # 裁剪实体：只保留选中的实体类型
    if target_entities:
        filtered_schema["entity_types"] = [
            et for et in master_schema.get("entity_types", [])
            if et["name"] in target_entities
        ]

    # 裁剪关系
    if target_relations:
        # 用户显式选择了关系类型
        filtered_schema["relation_types"] = [
            rt for rt in master_schema.get("relation_types", [])
            if rt["name"] in target_relations
        ]
    elif target_entities:
        # 用户只选了实体，自动推导：保留约束与选中实体类型集合有交集（或不约束）的关系
        # （用于 one-pass 合并抽取时的 prompt：只需要这些关系出现在提示词里）
        def _relevant(rt):
            src = relation_source_types(rt)
            tgt = relation_target_types(rt)
            src_ok = not src or bool(src & target_entities)
            tgt_ok = not tgt or bool(tgt & target_entities)
            return src_ok and tgt_ok
        filtered_schema["relation_types"] = [
            rt for rt in master_schema.get("relation_types", []) if _relevant(rt)
        ]

    return filtered_schema


def _validate_entities_strict(entities: List[Dict], schema: Dict) -> Tuple[List[Dict], List[Dict]]:
    """严格校验实体：类型必须在 Schema 中。

    返回 (valid, rejected)。被拒项带 _reject_reason，由调用方落库——
    规则否决 LLM 的提案必须可见（审计 + Schema 缺口信号），不允许静默丢弃。
    """
    allowed_types = {et["name"] for et in schema.get("entity_types", [])}
    valid, rejected = [], []
    for e in entities:
        if not e.get("name"):
            e["_reject_reason"] = "missing_name"
            rejected.append(e)
        elif e.get("entity_type") in allowed_types:
            valid.append(e)
        else:
            e["_reject_reason"] = "entity_type_not_in_schema"
            rejected.append(e)
    return valid, rejected


def _validate_relations_strict(
    relations: List[Dict],
    schema: Dict,
    name_index: Dict[str, List[Node]],
    chunk_types: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """严格校验关系：类型必须在 Schema 中，且符合源/目标实体类型约束（支持多类型约束）。

    实体引用按 (name, type) 复合键消解（同名多类型时按片段上下文与约束消歧）。
    返回 (valid, rejected)；valid 项附带 _source_node/_target_node 已消解节点。
    """
    rel_type_map = {rt["name"]: rt for rt in schema.get("relation_types", [])}

    valid, rejected = [], []
    for r in relations:
        r_type_name = r.get("relation_type")
        s_name = r.get("source_name")
        t_name = r.get("target_name")

        if r_type_name not in rel_type_map:
            r["_reject_reason"] = "relation_type_not_in_schema"
            rejected.append(r)
            continue

        rt_def = rel_type_map[r_type_name]
        src_types = relation_source_types(rt_def)
        tgt_types = relation_target_types(rt_def)

        s_node = _resolve_node_by_name(s_name, name_index, src_types, chunk_types)
        t_node = _resolve_node_by_name(t_name, name_index, tgt_types, chunk_types)

        if not s_node or not t_node:
            r["_reject_reason"] = "endpoint_not_found"
            rejected.append(r)
            continue

        # 校验约束（多类型：满足其一即可）
        if not type_satisfies(s_node.entity_type, src_types):
            r["_reject_reason"] = f"source_type_mismatch(要求{format_constraint(rt_def.get('source_entity_type'))},实际{s_node.entity_type})"
            rejected.append(r)
            continue
        if not type_satisfies(t_node.entity_type, tgt_types):
            r["_reject_reason"] = f"target_type_mismatch(要求{format_constraint(rt_def.get('target_entity_type'))},实际{t_node.entity_type})"
            rejected.append(r)
            continue

        r["_source_node"] = s_node
        r["_target_node"] = t_node
        valid.append(r)

    return valid, rejected


def _rank_global_candidates(chunk_text: str, candidates: List[Dict], limit: int) -> List[Dict]:
    """跨片段推断候选排序：与当前片段相关性优先，而非任取前 N 个。

    评分（确定性、无 LLM）：实体名逐字出现在片段中 > 字符二元组重叠率。
    """
    def _bigrams(s: str) -> Set[str]:
        s = s.strip()
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else {s} if s else set()

    text_bigrams = _bigrams(chunk_text)

    def score(cand: Dict) -> float:
        name = cand.get("name", "")
        if not name:
            return 0.0
        if name in chunk_text:
            return 2.0
        name_bigrams = _bigrams(name)
        if not name_bigrams:
            return 0.0
        return len(name_bigrams & text_bigrams) / len(name_bigrams)

    scored = [(score(c), i, c) for i, c in enumerate(candidates)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    # 过滤零分候选（与片段完全无关的实体只会给 prompt 添噪）
    top = [c for s, _, c in scored[:limit] if s > 0]
    return top


def _find_related_entity_types(new_entity_types: Set[str], master_schema: Dict) -> Dict[str, List[Dict]]:
    """
    根据 master_schema 的关系定义，找出与 new_entity_types 有关系连接的「对端」实体类型。
    支持多类型约束。返回: { 对端实体类型名 -> [关系定义列表] }
    """
    related = defaultdict(list)
    for rt in master_schema.get("relation_types", []):
        src_types = relation_source_types(rt)
        tgt_types = relation_target_types(rt)
        if src_types & new_entity_types:
            for peer in tgt_types - new_entity_types:
                related[peer].append(rt)
        if tgt_types & new_entity_types:
            for peer in src_types - new_entity_types:
                related[peer].append(rt)
    return dict(related)


async def _schema_driven_relation_linking(
    new_nodes: List,
    master_schema: Dict,
    entity_map: Dict,
    chunk_text: str,
    chunk_schema: Dict,
    mem_lock,
    stream_log: bool = False,
) -> List[Dict]:
    """
    基于 Schema 关系定义，将新抽取的实体与存量图谱中相关类型实体做关系推断。
    例如：新抽取了「审计指标A」，Schema 中定义了 审计要点→包含→审计指标，
    则去存量图谱中找所有「审计要点」实体，让 LLM 判断是否与审计指标A有关系。
    """
    new_entity_types = set(n.entity_type for n in new_nodes)
    related_types_map = _find_related_entity_types(new_entity_types, master_schema)

    if not related_types_map:
        return []

    # 收集存量中对端类型的实体
    existing_entities_by_type: Dict[str, List] = defaultdict(list)
    async with mem_lock:
        for node in entity_map.values():
            if node.entity_type in related_types_map:
                existing_entities_by_type[node.entity_type].append(
                    {"name": node.name, "entity_type": node.entity_type}
                )

    if not existing_entities_by_type:
        return []

    # 构建候选：新实体 + 对端存量实体
    new_entities_desc = [{"name": n.name, "entity_type": n.entity_type} for n in new_nodes]

    # 搜集所有涉及的关系类型定义
    involved_relations = []
    seen_rel_names = set()
    for rels in related_types_map.values():
        for rt in rels:
            if rt["name"] not in seen_rel_names:
                involved_relations.append(rt)
                seen_rel_names.add(rt["name"])

    # 收集对端存量实体（按与当前片段相关性排序，限制数量避免 prompt 过长）
    global_entities = []
    for etype, entities in existing_entities_by_type.items():
        global_entities.extend(_rank_global_candidates(chunk_text, entities, 30))

    if not global_entities:
        return []

    # 构建专用 schema 只包含涉及的关系
    linking_schema = {"relation_types": involved_relations}

    from services.extraction.relation import infer_cross_chunk_relations
    from services.extraction_logger import log_extraction

    log_extraction(
        f"[Schema链接] 新实体类型 {new_entity_types} 与存量 {list(existing_entities_by_type.keys())} "
        f"共 {len(global_entities)} 个实体做关系推断"
    )

    rels = await infer_cross_chunk_relations(
        chunk_text,
        new_entities_desc,
        global_entities,
        linking_schema,
        stream_log=stream_log,
    )
    return rels


def _report(callback: Optional[Callable], **kwargs):
    """报告进度"""
    if callback:
        try:
            callback(**kwargs)
        except Exception as e:
            print(f"[Pipeline] 进度回调失败: {e}")
