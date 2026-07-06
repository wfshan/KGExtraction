"""Schema 演化工作流（OntoKG / TRACE-KG 式诱导 + 人工确认版本化）。

流程：
1. detect_schema_gaps：对比草稿图谱中出现的类型与当前 Schema，检测缺口；
2. induce_schema_additions：用 LLM 为缺口诱导出实体/关系类型定义（候选）；
3. apply_schema_version：人工确认后写入新版 Schema，并保存版本快照。

强调「数据驱动诱导 + 人工治理 + 版本化」，而非全自动 Schema-free。
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import get_project_dir
from models.schema import SchemaConfig, EntityType, RelationType
from services.graph_store import load_draft_graph, _load_schema_dict
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL

logger = logging.getLogger(__name__)

RESERVED_ENTITY_TYPES = {"未归类片段", "文档片段", "未分类实体", "未知类型"}
RESERVED_RELATION_TYPES = {"下一段"}

COLORS = [
    "#4A90D9", "#50C878", "#FF6B6B", "#FFD93D",
    "#9B59B6", "#1ABC9C", "#E67E22", "#3498DB",
]


def _versions_dir(project_id: str) -> Path:
    d = get_project_dir(project_id) / "schema_versions"
    d.mkdir(exist_ok=True)
    return d


def detect_schema_gaps(project_id: str) -> Dict:
    """检测相对当前 Schema 的类型缺口。

    信号源有二：
    1. 草稿图谱中出现但 Schema 未定义的类型（历史数据）；
    2. 抽取阶段被严格校验拒绝的 out-of-schema 类型（被拒项存储）——
       这是最主要的缺口信号：LLM 反复提案某个类型说明文档里确实存在该概念。
    """
    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    entity_type_names = {et["name"] for et in schema.get("entity_types", [])}
    relation_type_names = {rt["name"] for rt in schema.get("relation_types", [])}

    graph = load_draft_graph(project_id)
    node_by_id = {n.id: n for n in graph.nodes}

    # 实体类型缺口
    entity_counter = Counter()
    entity_examples = defaultdict(list)
    fallback_chunks = 0
    for n in graph.nodes:
        et = n.entity_type
        if et in RESERVED_ENTITY_TYPES:
            if et == "未归类片段":
                fallback_chunks += 1
            continue
        if et not in entity_type_names:
            entity_counter[et] += 1
            if len(entity_examples[et]) < 5:
                entity_examples[et].append(n.name)

    # 关系类型缺口
    relation_counter = Counter()
    relation_pairs = defaultdict(Counter)
    relation_examples = defaultdict(list)
    for e in graph.edges:
        rt = e.relation_type
        if rt in RESERVED_RELATION_TYPES:
            continue
        if rt not in relation_type_names:
            relation_counter[rt] += 1
            s = node_by_id.get(e.source_id)
            t = node_by_id.get(e.target_id)
            if s and t:
                relation_pairs[rt][(s.entity_type, t.entity_type)] += 1
                if len(relation_examples[rt]) < 5:
                    relation_examples[rt].append(f"{s.name} --({rt})--> {t.name}")

    # 信号源 2：抽取阶段被拒的 out-of-schema 类型（高频被拒 = 强缺口信号）
    rejected_summary = {"total": 0}
    try:
        from services.rejected_store import rejected_stats
        rejected_summary = rejected_stats(project_id)
        for item in rejected_summary.get("entity_types", []):
            et = item["name"]
            if not et or et in entity_type_names or et in RESERVED_ENTITY_TYPES:
                continue
            entity_counter[et] += item["count"]
            for ex in item.get("examples", []):
                if len(entity_examples[et]) < 5 and ex not in entity_examples[et]:
                    entity_examples[et].append(ex)
        for item in rejected_summary.get("relation_types", []):
            rt = item["name"]
            if not rt or rt in relation_type_names or rt in RESERVED_RELATION_TYPES:
                continue
            relation_counter[rt] += item["count"]
            for ex in item.get("examples", []):
                if len(relation_examples[rt]) < 5 and ex not in relation_examples[rt]:
                    relation_examples[rt].append(ex)
    except Exception as e:
        logger.warning(f"[Schema演化] 读取被拒项失败: {e}")

    missing_entities = [
        {"name": et, "count": cnt, "examples": entity_examples.get(et, [])}
        for et, cnt in entity_counter.most_common()
    ]
    missing_relations = []
    for rt, cnt in relation_counter.most_common():
        pair = relation_pairs[rt].most_common(1)
        src, tgt = (pair[0][0] if pair else ("", ""))
        missing_relations.append({
            "name": rt, "count": cnt,
            "source_entity_type": src, "target_entity_type": tgt,
            "examples": relation_examples.get(rt, []),
        })

    return {
        "missing_entity_types": missing_entities,
        "missing_relation_types": missing_relations,
        "fallback_chunk_count": fallback_chunks,
        "rejected_total": rejected_summary.get("total", 0),
        "has_gaps": bool(missing_entities or missing_relations),
    }


INDUCE_PROMPT = """你是本体工程专家。下面是图谱中已出现、但当前 Schema 尚未定义的「候选类型」。
请为它们生成规范的类型定义，作为 Schema 演化建议（供人工确认）。

## 当前 Schema 实体类型
{current_entities}

## 当前 Schema 关系类型
{current_relations}

## 待诱导的实体类型缺口（含样例）
{missing_entities}

## 待诱导的关系类型缺口（含样例与两端类型）
{missing_relations}

## 输出（严格 JSON）
{{
  "entity_types": [{{"name": "类型名", "definition": "定义", "examples": ["示例"]}}],
  "relation_types": [{{"name": "关系名", "definition": "定义", "source_entity_type": "源", "target_entity_type": "目标", "examples": ["示例"]}}]
}}
"""


async def induce_schema_additions(project_id: str) -> Dict:
    """根据缺口诱导出 Schema 增补候选（不落库，供人工确认）。"""
    gaps = detect_schema_gaps(project_id)
    if not gaps["has_gaps"]:
        return {"entity_types": [], "relation_types": [], "note": "无 Schema 缺口"}

    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    try:
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是严谨的本体演化设计器，只返回 JSON。"},
                {"role": "user", "content": INDUCE_PROMPT.format(
                    current_entities=json.dumps([et["name"] for et in schema.get("entity_types", [])], ensure_ascii=False),
                    current_relations=json.dumps([rt["name"] for rt in schema.get("relation_types", [])], ensure_ascii=False),
                    missing_entities=json.dumps(gaps["missing_entity_types"], ensure_ascii=False, indent=2),
                    missing_relations=json.dumps(gaps["missing_relation_types"], ensure_ascii=False, indent=2),
                )},
            ],
            complexity=COMPLEXITY_NORMAL,
        )
        return {
            "entity_types": result.get("entity_types", []),
            "relation_types": result.get("relation_types", []),
            "gaps": gaps,
        }
    except Exception as e:
        logger.warning(f"[Schema演化] 诱导失败: {e}")
        # 回退：直接用缺口名生成占位定义
        return {
            "entity_types": [
                {"name": g["name"], "definition": f"图谱中出现的「{g['name']}」类型", "examples": g.get("examples", [])}
                for g in gaps["missing_entity_types"]
            ],
            "relation_types": [
                {"name": g["name"], "definition": f"图谱中出现的「{g['name']}」关系",
                 "source_entity_type": g.get("source_entity_type", ""), "target_entity_type": g.get("target_entity_type", ""),
                 "examples": g.get("examples", [])}
                for g in gaps["missing_relation_types"]
            ],
            "gaps": gaps,
        }


def apply_schema_version(project_id: str, schema: SchemaConfig, note: str = "") -> Dict:
    """人工确认后写入新 Schema，并保存版本化快照。"""
    schema_path = get_project_dir(project_id) / "schema.json"

    # 先把当前 Schema 存为一个历史版本（如果存在）
    versions = list_schema_versions(project_id)
    next_version = (max([v["version"] for v in versions]) + 1) if versions else 1

    snapshot = {
        "version": next_version,
        "created_at": datetime.now().isoformat(),
        "note": note,
        "schema": schema.model_dump(),
    }
    with open(_versions_dir(project_id) / f"v{next_version}.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema.model_dump(), f, ensure_ascii=False, indent=2)

    return {"version": next_version, "entity_types": len(schema.entity_types), "relation_types": len(schema.relation_types)}


def list_schema_versions(project_id: str) -> List[Dict]:
    d = _versions_dir(project_id)
    versions = []
    for f in sorted(d.glob("v*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            versions.append({
                "version": data.get("version"),
                "created_at": data.get("created_at"),
                "note": data.get("note", ""),
                "entity_types": len(data.get("schema", {}).get("entity_types", [])),
                "relation_types": len(data.get("schema", {}).get("relation_types", [])),
            })
        except Exception:
            continue
    return versions


def merge_additions_into_schema(project_id: str, additions: Dict) -> SchemaConfig:
    """将诱导出的增补候选合并进当前 Schema，返回合并后的 SchemaConfig（不落库）。"""
    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    existing_entities = {et["name"]: et for et in schema.get("entity_types", [])}
    existing_relations = {rt["name"]: rt for rt in schema.get("relation_types", [])}

    n_existing = len(existing_entities)
    for i, et in enumerate(additions.get("entity_types", [])):
        name = (et.get("name") or "").strip()
        if not name or name in existing_entities:
            continue
        existing_entities[name] = {
            "name": name,
            "definition": et.get("definition", ""),
            "examples": [str(x) for x in et.get("examples", [])],
            "color": COLORS[(n_existing + i) % len(COLORS)],
        }
    for rt in additions.get("relation_types", []):
        name = (rt.get("name") or "").strip()
        if not name or name in existing_relations:
            continue
        existing_relations[name] = {
            "name": name,
            "definition": rt.get("definition", ""),
            "source_entity_type": rt.get("source_entity_type", ""),
            "target_entity_type": rt.get("target_entity_type", ""),
            "examples": [str(x) for x in rt.get("examples", [])],
        }

    return SchemaConfig(
        entity_types=[EntityType(**et) for et in existing_entities.values()],
        relation_types=[RelationType(**rt) for rt in existing_relations.values()],
    )
