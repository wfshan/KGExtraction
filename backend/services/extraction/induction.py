"""归纳抽取原语（inductive 分道，v3 第②步）。

命名实体是"文本中存在、可定位"的表面形式；归纳知识（概念/规则/模式）是
"从案例中概括出来、原文并不存在"的抽象陈述。二者的抽取机制根本不同：

- induce_from_cases：从片段归纳出结构化的抽象知识（陈述 + 结构字段 + 源案例证据）。
- verify_faithfulness：判断每条归纳是否被源文本支撑，剔除幻觉归纳（对应逐字证据
  校验在归纳知识上的替代——归纳产物不会逐字命中原文，只能查"忠实度"）。
"""
import json
import logging
from typing import Dict, List

from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL, COMPLEXITY_COMPLEX
from services.extraction.prompts import (
    INDUCE_FROM_CASES_SYSTEM,
    INDUCE_FROM_CASES_USER,
    FAITHFULNESS_SYSTEM,
    FAITHFULNESS_USER,
    MERGE_SEMANTIC_SYSTEM,
    MERGE_SEMANTIC_USER,
)

logger = logging.getLogger(__name__)


def _support_to_confidence(n: int) -> float:
    """支撑案例数 → 可信度的确定性映射：单调递增、上界 0.95、渐近于 1。

    1 例=0.5、2 例=0.75、3 例≈0.83、5 例=0.9……让"越多片段独立归纳出同一知识越可信"
    成为客观依据，取代 LLM 自报置信度。
    """
    if n <= 0:
        return 0.5
    return round(min(0.95, 1.0 - 0.5 / n), 3)


def objectify_inductive_confidence(project_id: str) -> int:
    """归纳知识可信度客观化（抽取收尾调用）。

    以「支撑案例数 = 去重后来源片段数」重算归纳节点的 confidence，取代 induce_from_cases
    阶段沿用的 LLM 自报值。仅作用于 Schema 中标注为 inductive 的实体类型。
    返回被更新的节点数。
    """
    from services.graph_store import load_draft_graph, save_draft_graph, _load_schema_dict

    schema = _load_schema_dict(project_id)
    if not schema:
        return 0
    inductive_types = {
        et.get("name") for et in schema.get("entity_types", [])
        if et.get("abstractness") == "inductive"
    }
    if not inductive_types:
        return 0

    graph = load_draft_graph(project_id)
    updated = 0
    for node in graph.nodes:
        if node.entity_type in inductive_types:
            n = len([c for c in node.source_chunk_ids if c != "cold_start"])
            if n <= 0:
                continue
            node.confidence = _support_to_confidence(n)
            updated += 1
    if updated:
        save_draft_graph(project_id, graph)
    return updated


def _node_semantic_text(node) -> str:
    """归纳节点的语义表征文本：名称 + 结构字段值（用于嵌入相似度召回）。"""
    parts = [node.name]
    for k, v in (node.properties or {}).items():
        if k.startswith("_") or not v:
            continue
        parts.append(f"{k}:{v}")
    return " | ".join(str(p) for p in parts)[:512]


def _merge_nodes_into(rep, dup):
    """将 dup 节点的支撑证据并入代表节点 rep（同一条知识的不同措辞）。"""
    for cid in dup.source_chunk_ids:
        if cid not in rep.source_chunk_ids:
            rep.source_chunk_ids.append(cid)
    seen = {(q.get("chunk_id"), q.get("quote")) for q in rep.evidence_quotes}
    for q in dup.evidence_quotes:
        if (q.get("chunk_id"), q.get("quote")) not in seen:
            rep.evidence_quotes.append(q)
    # 结构字段：代表者优先，缺失的从被并者补齐
    for k, v in (dup.properties or {}).items():
        if k.startswith("_"):
            continue
        if not rep.properties.get(k) and v:
            rep.properties[k] = v
    aliases = rep.properties.setdefault("aliases", [])
    if dup.name != rep.name and dup.name not in aliases:
        aliases.append(dup.name)


async def merge_semantic_inductive(project_id: str, stream_log: bool = False) -> int:
    """归纳知识跨案例语义归并（merge_semantic 原语，抽取收尾调用）。

    同一条规则/概念被不同片段以不同措辞归纳时，名称消歧（表面对齐）无法去重，
    导致知识库膨胀、且"支撑案例数"被拆散无法累积。此处按类型做：
    嵌入相似度召回候选簇 → LLM 裁决"是否同一条知识" → 合并支撑证据与结构字段。
    合并后应再调用 objectify_inductive_confidence 以归并后的案例数重算可信度。

    返回被合并掉的节点数。LLM 裁决失败时保守跳过该簇（不误并）。
    """
    from services.graph_store import load_draft_graph, save_draft_graph, _load_schema_dict
    from services.embedding import embed_texts
    from models.graph import make_edge_id
    import numpy as np

    schema = _load_schema_dict(project_id)
    if not schema:
        return 0
    inductive_types = {
        et.get("name") for et in schema.get("entity_types", [])
        if et.get("abstractness") == "inductive"
    }
    if not inductive_types:
        return 0

    graph = load_draft_graph(project_id)
    merged_into: Dict[str, str] = {}  # 被并节点 id → 代表节点 id

    for etype in inductive_types:
        nodes = [n for n in graph.nodes if n.entity_type == etype]
        if len(nodes) < 2:
            continue

        # 1) 嵌入相似度召回候选簇（贪心并查：相似度 ≥ 阈值即视为同簇候选）
        try:
            vecs = embed_texts([_node_semantic_text(n) for n in nodes]).astype("float32")
        except Exception as e:
            logger.warning(f"[语义归并] 嵌入失败，跳过类型「{etype}」: {e}")
            continue
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
        sim = vecs @ vecs.T

        threshold = 0.80  # 召回候选的相似度下限；真正是否归并由 LLM 裁决
        parent = list(range(len(nodes)))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                if sim[i, j] >= threshold:
                    parent[_find(j)] = _find(i)

        clusters: Dict[int, List[int]] = {}
        for i in range(len(nodes)):
            clusters.setdefault(_find(i), []).append(i)
        candidate_clusters = [c for c in clusters.values() if 2 <= len(c) <= 12]
        if not candidate_clusters:
            continue

        # 2) 逐簇 LLM 裁决是否同一条知识
        for cluster in candidate_clusters:
            items_desc = "\n".join(
                f"{idx}: {_node_semantic_text(nodes[i])}" for idx, i in enumerate(cluster)
            )
            try:
                result = await llm_gateway.chat_json(
                    messages=[
                        {"role": "system", "content": MERGE_SEMANTIC_SYSTEM},
                        {"role": "user", "content": MERGE_SEMANTIC_USER.format(
                            entity_type=etype, items=items_desc)},
                    ],
                    complexity=COMPLEXITY_COMPLEX,
                    stream_log=stream_log,
                )
            except Exception as e:
                logger.warning(f"[语义归并] LLM 裁决失败，保守跳过该簇: {e}")
                continue

            for group in result.get("groups", []):
                indices = [i for i in group.get("indices", []) if isinstance(i, int) and 0 <= i < len(cluster)]
                if len(indices) < 2:
                    continue
                rep_local = group.get("representative")
                if not isinstance(rep_local, int) or rep_local not in indices:
                    rep_local = indices[0]
                rep = nodes[cluster[rep_local]]
                if rep.id in merged_into:  # 代表者已被并入他处，跳过避免链式混乱
                    continue
                for local in indices:
                    if local == rep_local:
                        continue
                    dup = nodes[cluster[local]]
                    if dup.id in merged_into or dup.id == rep.id:
                        continue
                    _merge_nodes_into(rep, dup)
                    merged_into[dup.id] = rep.id
                    logger.info(f"[语义归并] 「{dup.name}」→「{rep.name}」({etype})")

    if not merged_into:
        return 0

    # 3) 应用合并：删除被并节点，重定向并去重相关边
    graph.nodes = [n for n in graph.nodes if n.id not in merged_into]
    edges_by_id: Dict[str, object] = {}
    for e in graph.edges:
        e.source_id = merged_into.get(e.source_id, e.source_id)
        e.target_id = merged_into.get(e.target_id, e.target_id)
        if e.source_id == e.target_id:
            continue  # 归并产生的自环边丢弃
        e.id = make_edge_id(e.source_id, e.relation_type, e.target_id)
        if e.id in edges_by_id:
            kept = edges_by_id[e.id]
            for cid in e.source_chunk_ids:
                if cid not in kept.source_chunk_ids:
                    kept.source_chunk_ids.append(cid)
        else:
            edges_by_id[e.id] = e
    graph.edges = list(edges_by_id.values())

    save_draft_graph(project_id, graph)
    return len(merged_into)


def format_induce_types_desc(inductive_entity_types: List[Dict]) -> str:
    """格式化归纳类型定义（含结构模板），用于 prompt。"""
    lines = []
    for et in inductive_entity_types:
        line = f"- **{et['name']}**: {et.get('definition', '无定义')}"
        tmpl = et.get("structure_template") or {}
        fields = tmpl.get("fields") or []
        if fields:
            field_desc = "；".join(
                f"{f.get('key')}{'(必填)' if f.get('required') else ''}"
                + (f":{f.get('description')}" if f.get('description') else "")
                for f in fields
            )
            line += f"\n    结构字段：{field_desc}"
        if et.get("examples"):
            line += f"\n    示例：{', '.join(et['examples'])}"
        lines.append(line)
    return "\n".join(lines) if lines else "（无归纳类型）"


async def induce_from_cases(
    text: str,
    inductive_entity_types: List[Dict],
    stream_log: bool = False,
    extra_guidance: str = "",
) -> List[Dict]:
    """从文本归纳出符合目标类型的抽象知识。

    返回与 raw_entities 兼容的列表；每项附带 `_abstractness="inductive"` 标记，
    供后续证据分道（span 模式，不做逐字校验）与写图使用。
    """
    if not inductive_entity_types:
        return []

    system_content = INDUCE_FROM_CASES_SYSTEM.format(
        induce_types_desc=format_induce_types_desc(inductive_entity_types)
    )
    if extra_guidance:
        system_content += extra_guidance

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": INDUCE_FROM_CASES_USER.format(text=text)},
    ]

    # 归纳是较难的语义任务，用较强模型
    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_COMPLEX,
        stream_log=stream_log,
    )

    allowed = {et["name"] for et in inductive_entity_types}
    items = []
    for it in result.get("items", []):
        name = (it.get("name") or "").strip()
        etype = it.get("entity_type", "")
        if not name or etype not in allowed:
            continue
        it["name"] = name
        it["_abstractness"] = "inductive"
        items.append(it)

    logger.info(f"归纳抽取完成: {len(items)} 条抽象知识")
    return items


async def verify_faithfulness(
    text: str,
    induced_items: List[Dict],
    stream_log: bool = False,
) -> Dict[str, bool]:
    """判断每条归纳是否被源文本支撑。返回 {name: supported}。

    失败（异常）时保守放行（全部视为 supported），避免因校验故障丢失候选；
    真正的拦截由调用方按返回结果处理。
    """
    if not induced_items:
        return {}

    payload = [
        {"name": it.get("name", ""), "properties": it.get("properties", {})}
        for it in induced_items
    ]
    messages = [
        {"role": "system", "content": FAITHFULNESS_SYSTEM},
        {"role": "user", "content": FAITHFULNESS_USER.format(
            text=text[:2000],
            items=json.dumps(payload, ensure_ascii=False, indent=2),
        )},
    ]
    try:
        result = await llm_gateway.chat_json(
            messages=messages,
            complexity=COMPLEXITY_NORMAL,
            stream_log=stream_log,
        )
        verdict = {}
        for r in result.get("results", []):
            nm = (r.get("name") or "").strip()
            if nm:
                verdict[nm] = bool(r.get("supported", True))
        # 未被裁决到的默认放行
        return {it.get("name", ""): verdict.get(it.get("name", ""), True) for it in induced_items}
    except Exception as e:
        logger.warning(f"[归纳忠实度] 校验失败，保守放行: {e}")
        return {it.get("name", ""): True for it in induced_items}


def validate_structure(
    items: List[Dict],
    inductive_entity_types: List[Dict],
) -> tuple[List[Dict], List[Dict]]:
    """结构校验（确定性）：inductive 知识须齐备其类型的 required 结构字段。

    挡两类低质量归纳：
    - 残缺：缺少必填结构字段（如规则没有「触发条件」）；
    - 空泛：必填字段值为空或过短（无实质可判别内容）。

    返回 (valid, rejected)；rejected 项带 _reject_reason。
    """
    required_by_type: Dict[str, List[str]] = {}
    for et in inductive_entity_types:
        tmpl = et.get("structure_template") or {}
        required_by_type[et.get("name", "")] = [
            f.get("key") for f in tmpl.get("fields", []) if f.get("required") and f.get("key")
        ]

    valid, rejected = [], []
    for it in items:
        required = required_by_type.get(it.get("entity_type", ""), [])
        props = it.get("properties") or {}
        missing = [k for k in required if len(str(props.get(k, "")).strip()) < 2]
        if missing:
            r = dict(it)
            r["_reject_reason"] = f"missing_required_field:{','.join(missing)}"
            rejected.append(r)
        else:
            valid.append(it)
    return valid, rejected
