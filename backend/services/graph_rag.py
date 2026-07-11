"""
基于检索增强生成的"问图" (GraphRAG) 模块
结合纯文本 SQLite 和 NetworkX 内存图实现实体识别、子图扩展、和原文本查找
"""
import logging
import json
import asyncio
from typing import List, Dict, Any, Tuple, Optional
import networkx as nx

from services.llm_gateway import llm_gateway, COMPLEXITY_SIMPLE, COMPLEXITY_NORMAL
from services.graph_store import get_nx_graph, load_published_graph
from services.chunk_store import get_chunks_by_ids, search_chunks_fast
from services.chat_store import load_history, add_message
from services.fast_similarity import rank_entity_candidates, use_vector_similarity
from config import load_config, get_project_dir

logger = logging.getLogger(__name__)


def _get_mode_profile(retrieval_mode: str) -> Dict[str, int]:
    """
    不同问图模式下的上下文预算控制，避免提示词过长。
    """
    profiles = {
        "graph_flow": {
            "entity_recall_top_k": 5,
            "text_recall_top_k": 6,
            "max_edges_in_prompt": 20,
            "max_chunks_in_prompt": 6,
            "max_recall_nodes": 8,
            "max_recall_edges": 10,
            "max_recall_chunks": 5,
        },
        "graph_full": {
            "entity_recall_top_k": 6,
            "text_recall_top_k": 8,
            "max_edges_in_prompt": 16,
            "max_chunks_in_prompt": 5,
            "max_recall_nodes": 8,
            "max_recall_edges": 8,
            "max_recall_chunks": 4,
        },
        "graph_path": {
            "entity_recall_top_k": 6,
            "text_recall_top_k": 5,
            "max_edges_in_prompt": 12,
            "max_chunks_in_prompt": 4,
            "max_recall_nodes": 8,
            "max_recall_edges": 8,
            "max_recall_chunks": 4,
        },
        "hippo": {
            "entity_recall_top_k": 6,
            "text_recall_top_k": 6,
            "max_edges_in_prompt": 24,
            "max_chunks_in_prompt": 6,
            "max_recall_nodes": 15,
            "max_recall_edges": 15,
            "max_recall_chunks": 6,
            "ppr_top_nodes": 30,
        },
        "global": {
            "entity_recall_top_k": 0,
            "text_recall_top_k": 4,
            "max_edges_in_prompt": 0,
            "max_chunks_in_prompt": 3,
            "max_recall_nodes": 0,
            "max_recall_edges": 0,
            "max_recall_chunks": 3,
        },
        "text_only": {
            "entity_recall_top_k": 0,
            "text_recall_top_k": 8,
            "max_edges_in_prompt": 0,
            "max_chunks_in_prompt": 6,
            "max_recall_nodes": 0,
            "max_recall_edges": 0,
            "max_recall_chunks": 6,
        },
        "direct": {
            "entity_recall_top_k": 0,
            "text_recall_top_k": 0,
            "max_edges_in_prompt": 0,
            "max_chunks_in_prompt": 0,
            "max_recall_nodes": 0,
            "max_recall_edges": 0,
            "max_recall_chunks": 0,
        },
    }
    return profiles.get(retrieval_mode, profiles["graph_flow"])


_GLOBAL_QUERY_HINTS = ("整体", "总体", "全局", "主题", "概览", "总结", "讲了什么", "有哪些方面", "梳理", "overview")
_MULTIHOP_QUERY_HINTS = ("之间", "关联", "关系链", "路径", "如何影响", "间接", "多跳", "传导")


async def route_query(query: str, project_id: str) -> Dict[str, Any]:
    """按查询特征 + 意图识别自动选择检索模式（GraphRAG-Bench 共识：无单一最优架构）。

    路由规则：
    - 全局/主题型问题且已构建社区摘要 → global
    - 多跳关联/两个以上实体 → hippo（PPR 关联检索）
    - 命中具体实体 → graph_flow（子图扩展）
    - 未命中实体 → text_only（向量/关键词文本检索）
    """
    reason = ""
    # 1. 全局型问题
    if any(h in query for h in _GLOBAL_QUERY_HINTS):
        from services.community import load_communities
        if load_communities(project_id):
            return {"mode": "global", "reason": "全局/主题型问题，使用社区摘要"}
        reason = "全局型问题但未构建社区摘要，"

    # 2. 意图识别：提到的实体与类型
    intent = {}
    try:
        intent = await identify_intent_from_query(query, project_id)
    except Exception as e:
        logger.warning(f"[路由] 意图识别失败: {e}")
    entities = intent.get("target_entities", []) or []
    types = intent.get("target_types", []) or []

    multihop = any(h in query for h in _MULTIHOP_QUERY_HINTS)
    if len(entities) >= 2 and multihop:
        return {"mode": "hippo", "reason": reason + f"多实体（{len(entities)}）多跳关联问题，使用 PPR 关联检索", "intent": intent}
    if multihop and (entities or types):
        return {"mode": "hippo", "reason": reason + "多跳关联问题，使用 PPR 关联检索", "intent": intent}
    if entities or types:
        return {"mode": "graph_flow", "reason": reason + "命中具体实体/类型，使用子图扩展", "intent": intent}
    return {"mode": "text_only", "reason": reason + "未命中图谱实体，回退文本检索", "intent": intent}


async def identify_intent_from_query(query: str, project_id: str) -> Dict[str, Any]:
    """
    结合图谱 Schema 进行意图识别，识别用户提到的实体名称或实体类型。
    """
    # 获取本体结构
    from routers.schema import _load_schema
    schema = _load_schema(project_id)
    entity_types = [et.name for et in schema.entity_types]
    
    prompt = f"""
    你是一个知识图谱意图识别专家。请结合以下【图谱本体结构（Schema）】和【用户问题】，识别用户想要查询的具体实体名称或实体类型。
    
    ## 图谱本体结构（已定义的实体类型）：
    {entity_types}
    
    ## 用户问题：
    {query}
    
    请输出 JSON 格式，包含以下字段：
    - "target_entities": 用户明确提到的具体实体名称列表（如 "小米", "雷军"）。
    - "target_types": 用户提到的或隐含想要查询的实体类型列表（如 "公司", "人物"）。
    
    示例：
    用户问："小米公司的创始人是谁？" -> {{"target_entities": ["小米"], "target_types": ["人物"]}}
    用户问："列出所有的通信设备板块指标" -> {{"target_entities": [], "target_types": ["板块指标"]}}
    
    注意：请仅返回 JSON。
    """
    
    messages = [
        {"role": "system", "content": "你是一个精确的意图识别工具。"},
        {"role": "user", "content": prompt}
    ]
    
    try:
        intent = await llm_gateway.chat_json(messages, complexity=COMPLEXITY_SIMPLE)
        return intent
    except Exception as e:
        logger.error(f"意图识别失败: {e}")
        return {"target_entities": [], "target_types": []}


async def extract_entities_from_query(query: str) -> List[str]:
    """从用户查询中提取潜在实体"""
    prompt = f"""
    请从以下用户的问题中，提取出所有的核心实体名词（如人名、地名、组织机构名、专业术语等）。
    请仅返回实体列表的 JSON 数组，例如：["实体1", "实体2"]。如果未发现实体，返回空数组 []。
    
    用户问题：
    {query}
    """
    
    messages = [
        {"role": "system", "content": "你是一个精确的实体提取工具。"},
        {"role": "user", "content": prompt}
    ]
    
    try:
        entities = await llm_gateway.chat_json(messages, complexity=COMPLEXITY_SIMPLE)
        if isinstance(entities, list):
            return entities
        elif isinstance(entities, dict) and "entities" in entities:
            return entities["entities"]
    except Exception as e:
        logger.error(f"提取实体失败: {e}")
        
    return []

async def match_entities_in_graph(G: nx.DiGraph, query_entities: List[str], project_id: Optional[str] = None) -> List[str]:
    """在图谱字典中精确、模糊或语义匹配节点 ID。"""
    if not G or not query_entities:
        return []
        
    matched_node_ids = set()
    config = load_config()
    
    # 1. 构建快速的名称到 ID 的映射（用于精确和子串匹配）
    name_to_ids = {}
    entity_candidates = []
    for node_id, data in G.nodes(data=True):
        name = data.get("name")
        if name:
            name_to_ids.setdefault(name.lower(), []).append(node_id)
            entity_candidates.append({
                "node_id": node_id,
                "name": name,
                "entity_type": data.get("entity_type", ""),
            })
            
    # 2. 可选向量语义匹配（仅在 vector 模式）
    vector_store = None
    if project_id and use_vector_similarity(config):
        try:
            from services.vector_store import VectorStore
            v_store = VectorStore(get_project_dir(project_id), name="entity")
            # 简单检查索引是否存在
            if v_store.index_path.exists():
                vector_store = v_store
        except Exception as e:
            logger.warning(f"初始化向量库进行问图检索失败: {e}")

    for qe in query_entities:
        qe_lower = qe.lower()
        # 精确匹配优先
        if qe_lower in name_to_ids:
            matched_node_ids.update(name_to_ids[qe_lower])
    async def semantic_match(qe):
        if vector_store:
            try:
                from services.embedding import embed_text
                query_vec = await asyncio.to_thread(embed_text, qe)
                candidates = vector_store.search(
                    query_vec, 
                    top_k=5, 
                    score_threshold=config.score_threshold
                )
                local_matches = []
                for meta, score in candidates:
                    if "node_id" in meta:
                        local_matches.append(meta["node_id"])
                        logger.info(f"[Semantic Match] {qe} -> {meta.get('name')} (score: {score:.3f})")
                return local_matches
            except Exception as e:
                logger.error(f"语义匹配实体 {qe} 失败: {e}")
        elif entity_candidates:
            fast_candidates = rank_entity_candidates(
                query=qe,
                entities=entity_candidates,
                top_k=5,
                score_threshold=float(getattr(config, "fast_score_threshold", 0.25)),
            )
            return [meta.get("node_id") for meta, _ in fast_candidates if meta.get("node_id")]
        return []

    # 并发处理所有实体的匹配
    async def process_one_entity(qe):
        qe_lower = qe.lower()
        matches = set()
        # 精确匹配优先
        if qe_lower in name_to_ids:
            matches.update(name_to_ids[qe_lower])
        else:
            # 语义匹配
            sem_matches = await semantic_match(qe)
            matches.update(sem_matches)
            
            # 子串匹配保底
            if not matches and len(qe_lower) > 1:
                for name, ids in name_to_ids.items():
                    if qe_lower in name or name in qe_lower:
                        matches.update(ids)
        return matches

    results = await asyncio.gather(*[process_one_entity(qe) for qe in query_entities])
    for r in results:
        matched_node_ids.update(r)
                        
    return list(matched_node_ids)

def expand_subgraph(
    G: nx.DiGraph, 
    start_node_ids: List[str], 
    max_degree: int = 1,
    max_neighbors_per_node: int = 15,
    mode: str = "graph_flow"
) -> Tuple[List[Dict], List[Dict], List[str]]:
    """以一系列始发节点展开子图，获取邻居、边以及相关的原文 chunk_ids。增加了扇出限制以防图爆炸。"""
    if not G or not start_node_ids:
        return [], [], []
        
    visited_nodes = set()
    edges_found = []
    chunk_ids_set = set()
    
    # BFS
    queue = [(node_id, 0) for node_id in start_node_ids]
    # 如果是 graph_flow (独立流式)，我们需要记录每个节点是从哪个方向扩过来的
    # (node_id, depth, direction): direction 0=both, 1=up only, 2=down only
    if mode == "graph_flow":
        queue = [(node_id, 0, 0) for node_id in start_node_ids]

    print(f"\n[Trace] === GraphRAG 子图扩展开始 (模式: {mode}) ===")
    print(f"[Trace] 始发种子节点 (k={len(start_node_ids)}): {start_node_ids}")
    
    layer_counts = {}
    
    while queue:
        item = queue.pop(0)
        current_node, depth = item[0], item[1]
        direction = item[2] if len(item) > 2 else 0
        layer_counts[depth] = layer_counts.get(depth, 0) + 1
        
        if current_node in visited_nodes:
            continue
            
        visited_nodes.add(current_node)
        
        # 收集节点的 source chunks
        node_data = G.nodes.get(current_node, {})
        n_chunks = node_data.get("source_chunk_ids", [])
        if n_chunks:
            chunk_ids_set.update(n_chunks)
            
        if depth >= max_degree:
            continue
            
        # 扩展出边 (下游: successors)
        if mode != "graph_flow" or direction in (0, 2):
            successors = list(G.successors(current_node))
            if len(successors) > max_neighbors_per_node:
                successors = successors[:max_neighbors_per_node]
                
            for neighbor in successors:
                edge_data = G.edges[current_node, neighbor]
                edges_found.append({
                    "source": current_node,
                    "target": neighbor,
                    "relation": edge_data.get("relation_type", ""),
                })
                e_chunks = edge_data.get("source_chunk_ids", [])
                if e_chunks:
                    chunk_ids_set.update(e_chunks)
                if mode == "graph_flow":
                    queue.append((neighbor, depth + 1, 2)) # 继续向下
                else:
                    queue.append((neighbor, depth + 1))
            
        # 扩展入边 (上游: predecessors)
        if mode != "graph_flow" or direction in (0, 1):
            predecessors = list(G.predecessors(current_node))
            if len(predecessors) > max_neighbors_per_node:
                predecessors = predecessors[:max_neighbors_per_node]

            for neighbor in predecessors:
                edge_data = G.edges[neighbor, current_node]
                edges_found.append({
                    "source": neighbor,
                    "target": current_node,
                    "relation": edge_data.get("relation_type", ""),
                })
                e_chunks = edge_data.get("source_chunk_ids", [])
                if e_chunks:
                    chunk_ids_set.update(e_chunks)
                if mode == "graph_flow":
                    queue.append((neighbor, depth + 1, 1)) # 继续向上
                else:
                    queue.append((neighbor, depth + 1))
            
    print(f"[Trace] 扩展层级分布: {layer_counts}")
    print(f"[Trace] 子图展开完成: 最终节点总数={len(visited_nodes)}, 边总数={len(edges_found)}")
    # 格式化返回值
    nodes_info = []
    for n in visited_nodes:
        if n in G.nodes:
            d = G.nodes[n]
            nodes_info.append({"id": n, "name": d.get("name"), "type": d.get("entity_type")})
            
    # 规整出边的文字表达
    formatted_edges = []
    for e in edges_found:
        s_name = G.nodes[e["source"]].get("name", "Unknown")
        t_name = G.nodes[e["target"]].get("name", "Unknown")
        formatted_edges.append(f"[{s_name}] --({e['relation']})--> [{t_name}]")
        
    # 去重
    formatted_edges = list(set(formatted_edges))
            
    return nodes_info, formatted_edges, list(chunk_ids_set)

def _hippo_retrieve(
    G: nx.DiGraph,
    seed_node_ids: List[str],
    mode_profile: Dict[str, int],
) -> Tuple[List[Dict], List[str], List[str]]:
    """HippoRAG 式关联检索：以种子实体做 Personalized PageRank，
    取高激活节点构成关联子图，汇聚其原文片段。"""
    if not G or not seed_node_ids:
        return [], [], []

    valid_seeds = [n for n in seed_node_ids if n in G]
    if not valid_seeds:
        return [], [], []

    personalization = {n: 1.0 for n in valid_seeds}
    try:
        ppr = nx.pagerank(G, alpha=0.85, personalization=personalization, max_iter=100)
    except Exception as e:
        logger.warning(f"[HippoRAG] PPR 计算失败，回退 BFS: {e}")
        return expand_subgraph(G, valid_seeds, max_degree=1, mode="graph_flow")

    top_n = int(mode_profile.get("ppr_top_nodes", 30))
    ranked_nodes = sorted(ppr.items(), key=lambda x: x[1], reverse=True)
    # 保证种子始终入选
    selected = set(valid_seeds)
    for node_id, _score in ranked_nodes:
        if len(selected) >= top_n:
            break
        selected.add(node_id)

    nodes_info = []
    chunk_ids_set = set()
    for n in selected:
        d = G.nodes.get(n, {})
        nodes_info.append({"id": n, "name": d.get("name"), "type": d.get("entity_type"), "score": round(ppr.get(n, 0.0), 5)})
        for c in d.get("source_chunk_ids", []):
            chunk_ids_set.add(c)

    # 收集所选节点间的边
    formatted_edges = []
    sub_G = G.subgraph(selected)
    for u, v, data in sub_G.edges(data=True):
        s_name = G.nodes[u].get("name", "Unknown")
        t_name = G.nodes[v].get("name", "Unknown")
        formatted_edges.append(f"[{s_name}] --({data.get('relation_type', '')})--> [{t_name}]")
        for c in data.get("source_chunk_ids", []):
            chunk_ids_set.add(c)
    formatted_edges = list(set(formatted_edges))

    print(f"[Trace] HippoRAG PPR: 种子={len(valid_seeds)}, 激活节点={len(nodes_info)}, 边={len(formatted_edges)}")
    return nodes_info, formatted_edges, list(chunk_ids_set)


async def _build_global_context(
    project_id: str,
    query: str,
    mode_profile: Dict[str, int],
    session_id: str = "default",
) -> Tuple[List[Dict[str, str]], List[Dict], Dict]:
    """基于已构建的社区摘要回答全局/主题型问题。"""
    from services.community import load_communities

    communities = load_communities(project_id)
    if not communities:
        sys_prompt = (
            "你是一个知识图谱问答助手。当前项目尚未构建社区摘要，"
            "无法进行全局主题回答。请提示用户先在图谱发布后构建社区摘要，或改用其他检索模式。"
        )
        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": query}]
        return messages, [], {"mode": "global", "summary": {"total_nodes": 0, "total_edges": 0, "total_chunks": 0}, "nodes": [], "edges": [], "chunks": []}

    summaries_text = "\n\n".join(
        f"### 社区 {c.get('id')}（{c.get('size', 0)} 个实体）：{c.get('title', '')}\n{c.get('summary', '')}"
        for c in communities
    )
    sys_prompt = f"""你是基于知识图谱社区摘要进行全局推理的问答助手。
下面是整张图谱按社区聚类后生成的主题摘要，请基于这些摘要回答用户的全局性/主题性问题。
若问题需要具体细节而摘要不足以支撑，请明确说明。

[图谱社区摘要]
{summaries_text}
"""
    history = load_history(project_id, session_id)
    messages = [{"role": "system", "content": sys_prompt.strip()}]
    for msg in history:
        if msg["role"] == "system":
            messages[0]["content"] += f"\n\n[之前的对话总结]：\n{msg['content']}"
        else:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": query})

    recall_info = {
        "mode": "global",
        "summary": {"total_nodes": 0, "total_edges": 0, "total_chunks": len(communities)},
        "nodes": [],
        "edges": [],
        "chunks": [
            {"id": c.get("id"), "index": i, "text": (c.get("title", "") + ": " + c.get("summary", ""))[:120]}
            for i, c in enumerate(communities[: mode_profile.get("max_chunks_in_prompt", 3)])
        ],
    }
    return messages, [], recall_info


import time

async def build_context_prompt(
    project_id: str,
    query: str,
    max_degree: int = 1,
    max_start_entities: int = 5,
    retrieval_mode: str = "graph_flow",
    session_id: str = "default",
) -> Tuple[List[Dict[str, str]], List[Dict], Dict]:
    """构建携带 RAG 上下文的对话 Prompt；支持多种检索模式。"""
    start_time = time.time()
    logger.info(f"[GraphRAG] 开始构建上下文 (模式: {retrieval_mode})...")
    mode_profile = _get_mode_profile(retrieval_mode)
    
    # 模式 1: 直接回答 (不使用任何上下文)
    if retrieval_mode == "direct":
        return [{"role": "system", "content": "你是一个专家助手。"}, {"role": "user", "content": query}], [], {}

    # 模式 global: 基于社区摘要的全局问答（Microsoft GraphRAG 模式）
    if retrieval_mode == "global":
        return await _build_global_context(project_id, query, mode_profile, session_id=session_id)

    # 2. 只有 graph 相关的模式才需要进行图匹配
    nodes_info = []
    formatted_edges = []
    all_chunk_ids = []
    
    if retrieval_mode in ("graph_flow", "graph_full", "graph_path", "hippo"):
        # 0. 意图路由（结合 Schema 识别用户明确提到的实体/类型），作为高优先级种子
        matched_ids = []
        config = load_config()
        intent_type_filter: List[str] = []
        if bool(getattr(config, "enable_intent_routing", True)):
            try:
                intent = await identify_intent_from_query(query, project_id)
                intent_entities = intent.get("target_entities", []) or []
                intent_type_filter = intent.get("target_types", []) or []
                if intent_entities:
                    G_intent = get_nx_graph(project_id)
                    intent_ids = await match_entities_in_graph(G_intent, intent_entities, project_id=project_id)
                    for nid in intent_ids:
                        if nid not in matched_ids:
                            matched_ids.append(nid)
                    if matched_ids:
                        print(f"[Trace] 意图路由命中实体种子: {len(matched_ids)} 个 (类型倾向: {intent_type_filter})")
            except Exception as e:
                logger.warning(f"[GraphRAG] 意图路由失败，回退到语义召回: {e}")

        # 1. 向量化查询并召回实体 (从 entity.index 召回)
        logger.info(f"[GraphRAG] 正在根据问题语义召回实体起点 (k={max_start_entities})...")
        try:
            if use_vector_similarity(config):
                from services.vector_store import VectorStore
                from services.embedding import embed_text
                v_store = VectorStore(get_project_dir(project_id), name="entity")
                if v_store.index_path.exists():
                    query_vec = await asyncio.to_thread(embed_text, query)
                    candidates = v_store.search(
                        query_vec,
                        top_k=min(max_start_entities, mode_profile["entity_recall_top_k"] or max_start_entities),
                        score_threshold=getattr(config, "score_threshold", 0.3)
                    )
                    for meta, score in candidates:
                        if "node_id" in meta:
                            matched_ids.append(meta["node_id"])
                            print(f"[Trace] 向量匹配: {meta.get('name')} (id: {meta['node_id']}, score: {score:.3f})")
            else:
                G = get_nx_graph(project_id)
                entities = []
                for node_id, data in G.nodes(data=True):
                    name = data.get("name")
                    if not name:
                        continue
                    entities.append({
                        "node_id": node_id,
                        "name": name,
                        "entity_type": data.get("entity_type", ""),
                    })
                fast_candidates = rank_entity_candidates(
                    query=query,
                    entities=entities,
                    top_k=min(max_start_entities, mode_profile["entity_recall_top_k"] or max_start_entities),
                    score_threshold=float(getattr(config, "fast_score_threshold", 0.25)),
                )
                for meta, score in fast_candidates:
                    if "node_id" in meta:
                        matched_ids.append(meta["node_id"])
                        print(f"[Trace] 快速匹配: {meta.get('name')} (id: {meta['node_id']}, score: {score:.3f})")
        except Exception as e:
            logger.error(f"[GraphRAG] 向量召回实体失败: {e}")

        # 如果召回为空，尝试 LLM 兜底
        if not matched_ids:
            logger.info("[GraphRAG] 向量召回为空，尝试 LLM 实体提取兜底...")
            extracted_entities = await extract_entities_from_query(query)
            G = get_nx_graph(project_id)
            matched_ids = await match_entities_in_graph(G, extracted_entities, project_id=project_id)
            if max_start_entities > 0:
                matched_ids = matched_ids[:max_start_entities]

        # 去重并按意图类型倾向优先排序种子
        if matched_ids:
            seen_ids = set()
            deduped = []
            for nid in matched_ids:
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    deduped.append(nid)
            matched_ids = deduped
            if intent_type_filter:
                G_rank = get_nx_graph(project_id)
                matched_ids.sort(
                    key=lambda nid: 0 if G_rank.nodes.get(nid, {}).get("entity_type") in intent_type_filter else 1
                )

        # 3. 按配置深度展开子图
        if matched_ids:
            G = get_nx_graph(project_id)
            if retrieval_mode == "hippo":
                # HippoRAG 思路：以命中实体为种子做 Personalized PageRank 关联检索
                nodes_info, formatted_edges, all_chunk_ids = _hippo_retrieve(
                    G, matched_ids, mode_profile
                )
            elif retrieval_mode == "graph_path" and len(matched_ids) > 1:
                # 简单路径查找实现
                n1, n2 = matched_ids[0], matched_ids[1]
                try:
                    paths = list(nx.all_simple_paths(G, source=n1, target=n2, cutoff=max_degree))
                    path_nodes = set()
                    path_edges = []
                    for path in paths:
                        path_nodes.update(path)
                        for i in range(len(path) - 1):
                            u, v = path[i], path[i+1]
                            edge_data = G.edges[u, v]
                            path_edges.append(f"[{G.nodes[u]['name']}] --({edge_data.get('relation_type')})--> [{G.nodes[v]['name']}]")
                    nodes_info = [{"id": n, "name": G.nodes[n].get("name"), "type": G.nodes[n].get("entity_type")} for n in path_nodes]
                    formatted_edges = list(set(path_edges))
                    # 关联 chunks
                    tmp_chunk_ids = set()
                    for n in path_nodes:
                        tmp_chunk_ids.update(G.nodes[n].get("source_chunk_ids", []))
                    all_chunk_ids = list(tmp_chunk_ids)
                    print(f"[Trace] 路径发现: 节点数={len(path_nodes)}, 边数={len(formatted_edges)}")
                except:
                    nodes_info, formatted_edges, all_chunk_ids = expand_subgraph(G, matched_ids, max_degree=max_degree, mode="graph_flow")
            else:
                nodes_info, formatted_edges, all_chunk_ids = expand_subgraph(G, matched_ids, max_degree=max_degree, mode=retrieval_mode)
    
    # 如果是 text_only 或作为图模式的补充
    if retrieval_mode == "text_only" or not all_chunk_ids:
        config = load_config()
        if use_vector_similarity(config):
            from services.vector_store import VectorStore
            from services.embedding import embed_text

            chunk_v_store = VectorStore(get_project_dir(project_id), name="vector")
            if chunk_v_store.index_path.exists():
                v_start = time.time()
                query_vec = await asyncio.to_thread(embed_text, query)
                chunk_candidates = chunk_v_store.search(
                    query_vec,
                    top_k=max(1, mode_profile["text_recall_top_k"]),
                    score_threshold=0.3,
                )
                tmp_c_ids = [meta["chunk_id"] for meta, _ in chunk_candidates if "chunk_id" in meta]
                all_chunk_ids = list(set(all_chunk_ids + tmp_c_ids))
                print(f"[Trace] 文本向量检索完成: 召回数={len(tmp_c_ids)}, 耗时={time.time()-v_start:.3f}s")
        else:
            fast_chunks = search_chunks_fast(
                project_id=project_id,
                query=query,
                top_k=max(1, mode_profile["text_recall_top_k"]),
                score_threshold=float(getattr(config, "fast_score_threshold", 0.25)),
            )
            tmp_c_ids = [c.get("chunk_id") for c in fast_chunks if c.get("chunk_id")]
            all_chunk_ids = list(set(all_chunk_ids + tmp_c_ids))
            print(f"[Trace] 文本快速检索完成: 召回数={len(tmp_c_ids)}")
    
    # 4. 去查寻底层文本
    from services.chunk_store import get_chunks_by_ids
    max_chunks_in_prompt = max(1, mode_profile["max_chunks_in_prompt"])
    text_chunks = get_chunks_by_ids(project_id, all_chunk_ids[:max_chunks_in_prompt])
    
    # 5. 构建上下文
    # 侧重关系数据的展示
    graph_context = "暂无图谱结构上下文"
    limited_edges_for_prompt = formatted_edges[: mode_profile["max_edges_in_prompt"]]
    if limited_edges_for_prompt:
        print(f"[Trace] 构造上下文: 关联三元组数量={len(formatted_edges)}")
        graph_context = "识别到的实体关系三元组如下：\n" + "\n".join(limited_edges_for_prompt)
        
    text_context = "暂无原文上下文"
    if text_chunks:
        texts = [
            f"- 片段 {c.get('index_num', c.get('index', 0))}:\n  {c.get('content', '')}"
            for c in text_chunks
        ]
        text_context = "\n\n".join(texts)
        
    sys_prompt = f"""
    你是基于知识图谱进行推理与溯源的问答助手。本系统的工作流程是：结合本体结构识别意图 → 在图谱中匹配相应实体/类型并展开子图 → 拉取相关的原文片段。
    
    你当前拥有的上下文包括【图谱关系三元组】（包含实体及其关系属性）和【原文参考片段】。请结合这两者进行高一致性的推理回答。

    要求：
    1. 优先使用图谱中的结构化关系进行回答。
    2. 凡有依据处，请结合图谱中的关系与下方原文片段说明。
    3. 若所给信息不足以回答问题，请直接说明。

    [知识图谱关系三元组上下文]：
    {graph_context}

    [原始参考文本上下文]：
    {text_context}
    """
    
    # 获取历史记录
    history = load_history(project_id, session_id)
    
    # 重组消息
    messages = [{"role": "system", "content": sys_prompt.strip()}]
    
    # 添加历史记录
    for msg in history:
        # 如果历史里第一条是压缩 summary，我们将其作为补充上下文
        if msg["role"] == "system":
            messages[0]["content"] += f"\n\n[之前的对话总结（长期记忆）]：\n{msg['content']}"
        else:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
    # 添加当前用户查询
    messages.append({"role": "user", "content": query})

    # 构造召回元数据供前端展示
    recall_info = {
        "mode": retrieval_mode,
        "summary": {
            "total_nodes": len(nodes_info),
            "total_edges": len(formatted_edges),
            "total_chunks": len(text_chunks),
        },
        "nodes": nodes_info[: mode_profile["max_recall_nodes"]],
        "edges": limited_edges_for_prompt[: mode_profile["max_recall_edges"]],
        "chunks": [
            {
                "id": c.get("chunk_id"),
                "index": c.get("index_num"),
                "text": c.get("content", "")[:120],
            }
            for c in text_chunks[: mode_profile["max_recall_chunks"]]
        ],
    }
    
    print(f"[Trace] 上下文构建完毕: {len(nodes_info)} 实体, {len(formatted_edges)} 关系, {len(text_chunks)} 文本片段, 总耗时={time.time()-start_time:.3f}s")
    print(f"[Trace] === GraphRAG 子图扩展结束 ===\n")
    
    return messages, text_chunks, recall_info

async def stream_chat_rag(
    project_id: str,
    query: str,
    max_degree: int = 2,
    max_start_entities: int = 5,
    retrieval_mode: str = "graph_flow",
    session_id: str = "default",
):
    """进行 RAG 问答，并流式返回结果；支持可配置的检索深度与起点实体数。

    retrieval_mode="auto"（默认推荐）时按查询特征自动路由到合适的检索模式。
    session_id 隔离多会话/多用户的对话历史。
    """
    # 1. 保存用户的提问到历史
    await add_message(project_id, "user", query, session_id=session_id)

    # 1.5 自动路由
    if retrieval_mode == "auto":
        try:
            routing = await route_query(query, project_id)
            retrieval_mode = routing.get("mode", "graph_flow")
            yield f"【智能路由 → {retrieval_mode}：{routing.get('reason', '')}】\n\n"
        except Exception as e:
            logger.warning(f"[路由] 自动路由失败，回退 graph_flow: {e}")
            retrieval_mode = "graph_flow"

    # 2. 构建 Prompt（按配置的检索深度与起点实体数）
    if retrieval_mode != "direct":
        yield f"【正在使用 {retrieval_mode} 模式检索关联信息...】\n\n"
    
    try:
        messages, _, recall_info = await build_context_prompt(
            project_id,
            query,
            max_degree=max_degree,
            max_start_entities=max_start_entities,
            retrieval_mode=retrieval_mode,
            session_id=session_id,
        )
        # 3. 此时已经拿到召回信息，先发给前端一个特殊的标记包
        yield f"__RECALL_START__{json.dumps(recall_info, ensure_ascii=False)}__RECALL_END__"
    except Exception as e:
        logger.error(f"构建 GraphRAG 上下文失败: {e}")
        yield f"【检索增强失败，将使用基础模型直接回答】\n\n"
        messages = [{"role": "system", "content": "你是一个通用的问答助手。"}, {"role": "user", "content": query}]

    # 4. 流式生成
    full_response = ""
    async for chunk in llm_gateway.chat_stream(messages, complexity=COMPLEXITY_NORMAL):
        full_response += chunk
        yield chunk
        
    # 5. 生成完毕后，保存助手的回答到历史
    await add_message(project_id, "assistant", full_response, session_id=session_id)
