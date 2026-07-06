"""
LangGraph 抽取工作流编排
主流程：文档分片 → 循环（实体抽取 → 消歧 → 关系推断 → 自我修正 → 更新图谱）
"""
import json
import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set
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
from services.graph_store import (
    load_draft_graph,
    get_draft_entity_map,
    add_nodes_to_draft,
    add_edges_to_draft,
)

logger = logging.getLogger(__name__)

# 保底实体类型：未抽到任何本体实体的片段挂载到该类型节点上
FALLBACK_ENTITY_TYPE = "未归类片段"
# 文档结构：每个片段一个锚点节点，用该关系表示“下一段”
SEGMENT_ENTITY_TYPE = "文档片段"
NEXT_SEGMENT_RELATION = "下一段"


def _make_evidence(chunk_id: str, raw_evidence, enabled: bool) -> List[Dict]:
    """将 LLM 返回的 evidence 字段规整为 evidence_quotes 列表，受配置开关控制。"""
    if not enabled or not raw_evidence:
        return []
    quotes: List[str] = []
    if isinstance(raw_evidence, str):
        quotes = [raw_evidence]
    elif isinstance(raw_evidence, list):
        quotes = [str(q) for q in raw_evidence if q]
    return [{"chunk_id": chunk_id, "quote": q.strip()[:300]} for q in quotes if q and q.strip()]


async def run_extraction_pipeline_sync(
    project_id: str,
    run_id: str,
    progress_callback: Optional[Callable] = None,
    initial_stats: Optional[Dict] = None,
    skip_chunks: int = 0,
    only_doc_ids: Optional[List[str]] = None,
):
    """
    执行完整的抽取工作流（异步协程并发）。

    only_doc_ids 非空时仅处理这些文档的分片（增量摄入 / delta merge），
    新抽取的实体会与既有草稿图谱按名称合并，不重建全图。
    """
    project_dir = get_project_dir(project_id)
    config = load_config()

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

    # 获取全图所有草稿实体
    entity_map = await get_draft_entity_map(project_id)

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
        "tokens_used": 0,
        "timing_extract_ms": 0.0,
        "timing_disambiguation_ms": 0.0,
        "timing_relations_ms": 0.0,
        "timing_self_correction_ms": 0.0,
        "timing_total_chunk_ms": 0.0,
    }
    all_stats["total_chunks"] = total_chunks
    all_stats.setdefault("timing_extract_ms", 0.0)
    all_stats.setdefault("timing_disambiguation_ms", 0.0)
    all_stats.setdefault("timing_relations_ms", 0.0)
    all_stats.setdefault("timing_self_correction_ms", 0.0)
    all_stats.setdefault("timing_total_chunk_ms", 0.0)

    orphan_chunk_ids = set()
    mem_lock = asyncio.Lock()
    
    # 批量缓冲区
    node_buffer = []
    edge_buffer = []

    async def flush_buffer():
        nonlocal node_buffer, edge_buffer
        if node_buffer:
            await add_nodes_to_draft(project_id, node_buffer)
            node_buffer = []
        if edge_buffer:
            await add_edges_to_draft(project_id, edge_buffer)
            edge_buffer = []

    async def process_chunk(chunk_index: int, chunk: dict):
        set_run_context(project_id, run_id)
        chunk_text = chunk["text"]
        chunk_id = chunk["id"]
        
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
            if config.extraction_mode == "one-pass":
                log_extraction(f"[片段 {chunk_id}] 合并抽取实体与关系...")
                combined_result = await extract_entities_and_relations(
                    chunk_text,
                    chunk_schema,
                    stream_log=llm_stream_log,
                    extra_guidance=reflection_hint,
                )
                raw_entities = combined_result.get("entities", [])
                raw_relations = combined_result.get("relations", [])
            else:
                log_extraction(f"[片段 {chunk_id}] 实体抽取中...")
                raw_entities = await extract_entities(
                    chunk_text,
                    chunk_schema,
                    stream_log=llm_stream_log,
                    extra_guidance=reflection_hint,
                )
                raw_relations = [] # 随后提取
            t_extract_ms = (perf_counter() - t_extract_begin) * 1000
            async with mem_lock:
                all_stats["timing_extract_ms"] += t_extract_ms
            
            # --- 严格实体校验 ---
            raw_entities = _validate_entities_strict(raw_entities, chunk_schema)
            log_extraction(f"[片段 {chunk_id}] 经校验后保留 {len(raw_entities)} 个有效实体")

            new_nodes = []
            async with mem_lock:
                all_stats["entities_extracted"] += len(raw_entities)

            # 先做名称去重与已有实体命中，再做批量消歧，减少 LLM 次数
            pending_entities = []
            disambiguation_entities = []
            seen_names_in_chunk = set()
            disambiguation_low_conf_only = bool(getattr(config, "disambiguation_low_confidence_only", False))
            disambiguation_conf_threshold = float(getattr(config, "disambiguation_entity_confidence_threshold", 0.85))
            async with mem_lock:
                for entity_data in raw_entities:
                    entity_name = entity_data.get("name", "")
                    if not entity_name:
                        continue

                    # 同片段同名实体直接去重
                    if entity_name in seen_names_in_chunk:
                        all_stats["entities_deduplicated"] += 1
                        continue
                    seen_names_in_chunk.add(entity_name)

                    # 先与全局实体表做快速命中
                    if entity_name in entity_map:
                        existing_node = entity_map[entity_name]
                        if chunk_id not in existing_node.source_chunk_ids:
                            existing_node.source_chunk_ids.append(chunk_id)
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
                            current_match_node = entity_map.get(match_node.name)
                            if current_match_node and chunk_id not in current_match_node.source_chunk_ids:
                                current_match_node.source_chunk_ids.append(chunk_id)
                                all_stats["entities_deduplicated"] += 1
                        continue

                async with mem_lock:
                    # 并发下二次检查，避免重复写入
                    if entity_name in entity_map:
                        existing_node = entity_map[entity_name]
                        if chunk_id not in existing_node.source_chunk_ids:
                            existing_node.source_chunk_ids.append(chunk_id)
                        all_stats["entities_deduplicated"] += 1
                        continue

                    node = Node(
                        name=entity_name,
                        entity_type=entity_type,
                        properties=entity_data.get("properties", {}),
                        source_chunk_ids=[chunk_id],
                        evidence_quotes=_make_evidence(chunk_id, entity_data.get("evidence"), config.enable_evidence_anchor),
                        confidence=entity_data.get("confidence", 1.0)
                    )
                    entity_map[entity_name] = node
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
                chunk_entity_list = [{"name": e.get("name", ""), "entity_type": e.get("entity_type", "")} for e in raw_entities if e.get("name") in entity_map]

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
                        global_sample = [{"name": n.name, "entity_type": n.entity_type} for name, n in entity_map.items() if name not in {e["name"] for e in chunk_entity_list}]
                if global_sample:
                    try:
                        t_rel_begin = perf_counter()
                        cross_rels = await infer_cross_chunk_relations(
                            chunk_text,
                            chunk_entity_list,
                            global_sample[:20],
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

            # --- 严格关系校验 ---
            async with mem_lock:
                raw_relations = _validate_relations_strict(raw_relations, chunk_schema, entity_map)
            log_extraction(f"[片段 {chunk_id}] 经校验后保留 {len(raw_relations)} 条有效关系")

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
                        final_relations = _validate_relations_strict(res.get("relations", []), chunk_schema, entity_map)
                except Exception: pass

            new_edges = []
            async with mem_lock:
                for rel in final_relations:
                    s_name, t_name = rel.get("source_name", ""), rel.get("target_name", "")
                    if s_name in entity_map and t_name in entity_map:
                        edge = Edge(source_id=entity_map[s_name].id, target_id=entity_map[t_name].id, relation_type=rel.get("relation_type", ""), properties=rel.get("properties", {}), source_chunk_ids=[chunk_id], evidence_quotes=_make_evidence(chunk_id, rel.get("evidence"), config.enable_evidence_anchor), confidence=rel.get("confidence", 1.0))
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
                fallback_node = Node(id=chunk_id, name=f"片段-{doc_id[:8]}-{chunk_index}", entity_type=FALLBACK_ENTITY_TYPE, properties={}, source_chunk_ids=[chunk_id], confidence=0.5)
                async with mem_lock:
                    entity_map[fallback_node.name] = fallback_node
                    orphan_chunk_ids.add(chunk_id)
                    node_buffer.append(fallback_node)
                    if len(node_buffer) >= config.database_batch_size:
                        await flush_buffer()

            async with mem_lock:
                all_stats["timing_total_chunk_ms"] += (perf_counter() - chunk_begin) * 1000
                all_stats["processed_chunks"] += 1
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

    # 文档结构固化：为每个片段建立「文档片段」锚点（非保底片段）并添加「下一段」边，使图谱保留段落/顺序信息
    await _add_document_structure(project_id, all_chunks, orphan_chunk_ids, add_nodes_to_draft, add_edges_to_draft)

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
    _report(progress_callback,
            progress=95.0,
            current_step="抽取完成，生成统计信息",
            stats=all_stats)

    print(
        f"[Pipeline] ✅ 抽取完成: {all_stats['entities_extracted']} 实体, "
        f"{all_stats['relations_extracted']} 关系, "
        f"{all_stats['entities_deduplicated']} 去重"
    )


async def _add_document_structure(
    project_id: str,
    all_chunks: List[Dict],
    orphan_chunk_ids: Set[str],
    add_nodes_to_draft: Callable,
    add_edges_to_draft: Callable,
):
    """
    固化文档结构到图谱：为每个片段建立顺序锚点（非保底片段用「文档片段」节点），
    并添加「下一段」边，使标题/段落顺序等信息在图谱中可被检索与推理。
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
        # 用户只选了实体，自动推导：保留两端都在选中实体类型集合内的关系
        # （用于 one-pass 合并抽取时的 prompt：只需要这些关系出现在提示词里）
        filtered_schema["relation_types"] = [
            rt for rt in master_schema.get("relation_types", [])
            if rt.get("source_entity_type") in target_entities
            and rt.get("target_entity_type") in target_entities
        ]
        
    return filtered_schema


def _validate_entities_strict(entities: List[Dict], schema: Dict) -> List[Dict]:
    """严格校验实体：类型必须在 Schema 中"""
    allowed_types = {et["name"] for et in schema.get("entity_types", [])}
    valid = []
    for e in entities:
        if e.get("entity_type") in allowed_types:
            valid.append(e)
    return valid


def _validate_relations_strict(relations: List[Dict], schema: Dict, entity_map: Dict) -> List[Dict]:
    """严格校验关系：类型必须在 Schema 中，且符合源/目标实体类型约束"""
    rel_type_map = {rt["name"]: rt for rt in schema.get("relation_types", [])}
    
    valid = []
    for r in relations:
        r_type_name = r.get("relation_type")
        s_name = r.get("source_name")
        t_name = r.get("target_name")
        
        if r_type_name not in rel_type_map:
            continue
            
        rt_def = rel_type_map[r_type_name]
        
        # 从全局 entity_map 中查找源和目标实体的类型
        s_node = entity_map.get(s_name)
        t_node = entity_map.get(t_name)
        
        if not s_node or not t_node:
            continue
            
        s_type = s_node.entity_type
        t_type = t_node.entity_type
        
        # 校验约束
        match_src = not rt_def.get("source_entity_type") or s_type == rt_def["source_entity_type"]
        match_tgt = not rt_def.get("target_entity_type") or t_type == rt_def["target_entity_type"]
        
        if match_src and match_tgt:
            valid.append(r)
            
    return valid


def _find_related_entity_types(new_entity_types: Set[str], master_schema: Dict) -> Dict[str, List[Dict]]:
    """
    根据 master_schema 的关系定义，找出与 new_entity_types 有关系连接的「对端」实体类型。
    返回: { 对端实体类型名 -> [关系定义列表] }
    """
    related = defaultdict(list)
    for rt in master_schema.get("relation_types", []):
        src_type = rt.get("source_entity_type", "")
        tgt_type = rt.get("target_entity_type", "")
        if src_type in new_entity_types and tgt_type and tgt_type not in new_entity_types:
            related[tgt_type].append(rt)
        elif tgt_type in new_entity_types and src_type and src_type not in new_entity_types:
            related[src_type].append(rt)
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
        for name, node in entity_map.items():
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
    
    # 收集对端存量实体（限制数量避免 prompt 过长）
    global_entities = []
    for etype, entities in existing_entities_by_type.items():
        global_entities.extend(entities[:30])  # 每类最多 30 个
    
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
