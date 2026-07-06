"""
关系推断模块
支持文本内关系抽取和跨片段关系推理
"""
import json
import logging
from typing import Dict, List

from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL, COMPLEXITY_COMPLEX
from services.extraction.prompts import (
    RELATION_EXTRACTION_SYSTEM,
    RELATION_EXTRACTION_USER,
    CROSS_CHUNK_RELATION_SYSTEM,
    CROSS_CHUNK_RELATION_USER,
)

logger = logging.getLogger(__name__)


def format_relation_types_desc(schema: Dict) -> str:
    """格式化关系类型描述（支持多 domain/range 约束）"""
    from services.schema_utils import format_constraint

    lines = []
    for rt in schema.get("relation_types", []):
        line = f"- **{rt['name']}**: {rt.get('definition', '无定义')}"
        src_desc = format_constraint(rt.get("source_entity_type"))
        tgt_desc = format_constraint(rt.get("target_entity_type"))
        if src_desc != "*" or tgt_desc != "*":
            line += f"（{src_desc} → {tgt_desc}）"
        if rt.get("examples"):
            line += f"  示例: {', '.join(rt['examples'])}"
        lines.append(line)
    return "\n".join(lines) if lines else "（未定义关系类型，请自由抽取）"


async def extract_relations(
    text: str,
    entities: List[Dict],
    schema: Dict,
    stream_log: bool = False,
    extra_guidance: str = "",
) -> List[Dict]:
    """
    从文本中抽取实体间的关系

    Args:
        text: 文本片段
        entities: 当前文本中的实体列表
        schema: Schema 配置

    Returns:
        关系列表 [{"source_name", "target_name", "relation_type", "properties", "confidence"}]
    """
    if not entities or len(entities) < 2:
        return []

    relation_types_desc = format_relation_types_desc(schema)
    entities_desc = json.dumps(
        [{"name": e["name"], "type": e["entity_type"]} for e in entities],
        ensure_ascii=False,
    )

    system_content = RELATION_EXTRACTION_SYSTEM.format(
        relation_types_desc=relation_types_desc,
        entities_in_context=entities_desc,
    )
    if extra_guidance:
        system_content += extra_guidance

    messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": RELATION_EXTRACTION_USER.format(text=text),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_NORMAL,
        stream_log=stream_log,
    )

    relations = result.get("relations", [])
    logger.info(f"从文本中抽取了 {len(relations)} 个关系")
    return relations


async def infer_cross_chunk_relations(
    text: str,
    current_entities: List[Dict],
    global_entities: List[Dict],
    schema: Dict,
    stream_log: bool = False,
) -> List[Dict]:
    """
    跨片段关系推断

    Args:
        text: 当前文本片段
        current_entities: 当前片段中的实体
        global_entities: 全局图谱中的候选关联实体
        schema: Schema 配置

    Returns:
        推断的关系列表
    """
    if not current_entities or not global_entities:
        return []

    relation_types_desc = format_relation_types_desc(schema)

    current_desc = json.dumps(
        [{"name": e["name"], "type": e["entity_type"]} for e in current_entities],
        ensure_ascii=False,
    )
    global_desc = json.dumps(
        [{"name": e["name"], "type": e["entity_type"]} for e in global_entities[:20]],
        ensure_ascii=False,
    )

    messages = [
        {
            "role": "system",
            "content": CROSS_CHUNK_RELATION_SYSTEM.format(
                relation_types_desc=relation_types_desc,
                current_entities=current_desc,
                global_entities=global_desc,
            ),
        },
        {
            "role": "user",
            "content": CROSS_CHUNK_RELATION_USER.format(text=text[:500]),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_COMPLEX,
        stream_log=stream_log,
    )

    relations = result.get("relations", [])
    # 过滤低置信度
    relations = [r for r in relations if r.get("confidence", 0) >= 0.7]
    logger.info(f"跨片段推断了 {len(relations)} 个关系")
    return relations
