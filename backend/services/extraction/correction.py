"""
自我修正模块
对抽取结果进行自动校验与修复
"""
import json
import logging
from typing import Dict, List

from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL
from services.extraction.prompts import (
    SELF_CORRECTION_SYSTEM,
    SELF_CORRECTION_USER,
)

logger = logging.getLogger(__name__)


async def self_correct(
    entities: List[Dict],
    relations: List[Dict],
    schema: Dict,
    stream_log: bool = False,
) -> Dict:
    """
    自我修正 - 校验并修复抽取结果

    Args:
        entities: 实体列表
        relations: 关系列表
        schema: Schema 配置

    Returns:
        {
            "entities": 修正后的实体列表,
            "relations": 修正后的关系列表,
            "corrections_made": 修正数量,
        }
    """
    # 先做规则校验
    entities, relations, rule_corrections = _rule_based_correction(entities, relations, schema)

    # 再用 LLM 做深层校验
    if entities:
        try:
            llm_corrections = await _llm_correction(entities, relations, schema, stream_log=stream_log)
            entities, relations = _apply_corrections(entities, relations, llm_corrections)
            total_corrections = rule_corrections + len(llm_corrections)
        except Exception as e:
            logger.warning(f"LLM 校验失败，仅使用规则校验: {e}")
            total_corrections = rule_corrections
    else:
        total_corrections = rule_corrections

    return {
        "entities": entities,
        "relations": relations,
        "corrections_made": total_corrections,
    }


def _rule_based_correction(
    entities: List[Dict],
    relations: List[Dict],
    schema: Dict,
) -> tuple:
    """基于规则的快速校验"""
    corrections = 0
    entity_type_names = {et["name"] for et in schema.get("entity_types", [])}
    relation_type_names = {rt["name"] for rt in schema.get("relation_types", [])}

    # 过滤无效实体
    valid_entities = []
    for e in entities:
        if not e.get("name") or not e.get("entity_type"):
            corrections += 1
            continue
        # 如果 Schema 有约束，过滤不匹配的类型
        if entity_type_names and e["entity_type"] not in entity_type_names:
            corrections += 1
            continue
        valid_entities.append(e)

    # 去重（名称+类型相同的合并）
    seen = set()
    deduped_entities = []
    for e in valid_entities:
        key = (e["name"], e["entity_type"])
        if key not in seen:
            seen.add(key)
            deduped_entities.append(e)
        else:
            corrections += 1

    # 过滤无效关系
    entity_names = {e["name"] for e in deduped_entities}
    valid_relations = []
    for r in relations:
        if not r.get("source_name") or not r.get("target_name") or not r.get("relation_type"):
            corrections += 1
            continue
        if r["source_name"] not in entity_names or r["target_name"] not in entity_names:
            corrections += 1
            continue
        if relation_type_names and r["relation_type"] not in relation_type_names:
            corrections += 1
            continue
        valid_relations.append(r)

    return deduped_entities, valid_relations, corrections


async def _llm_correction(
    entities: List[Dict],
    relations: List[Dict],
    schema: Dict,
    stream_log: bool = False,
) -> List[Dict]:
    """使用 LLM 进行深层校验"""
    schema_desc = json.dumps(schema, ensure_ascii=False, indent=2)
    entities_str = json.dumps(entities, ensure_ascii=False, indent=2)
    relations_str = json.dumps(relations, ensure_ascii=False, indent=2)

    messages = [
        {
            "role": "system",
            "content": SELF_CORRECTION_SYSTEM.format(schema_desc=schema_desc),
        },
        {
            "role": "user",
            "content": SELF_CORRECTION_USER.format(
                entities=entities_str,
                relations=relations_str,
            ),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_NORMAL,
        stream_log=stream_log,
    )

    return result.get("corrections", [])


def _apply_corrections(
    entities: List[Dict],
    relations: List[Dict],
    corrections: List[Dict],
) -> tuple:
    """应用 LLM 建议的修正"""
    for c in corrections:
        action = c.get("action", "")
        target = c.get("target_name", "")
        ctype = c.get("type", "")

        if action == "remove":
            if ctype == "entity":
                entities = [e for e in entities if e.get("name") != target]
                relations = [r for r in relations if r.get("source_name") != target and r.get("target_name") != target]
            elif ctype == "relation":
                relations = [r for r in relations if not (r.get("source_name") == target or r.get("relation_type") == target)]

        elif action == "modify" and c.get("new_value"):
            if ctype == "entity":
                for e in entities:
                    if e.get("name") == target:
                        e.update(c["new_value"])
            elif ctype == "relation":
                for r in relations:
                    if r.get("source_name") == target:
                        r.update(c["new_value"])

    return entities, relations
