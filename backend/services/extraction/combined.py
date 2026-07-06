"""
合并抽取模块 (One-Pass)
一次调用同时抽取实体和关系
"""
import logging
from typing import Any, Dict, List, Tuple

from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL
from services.extraction.entity import format_entity_types_desc
from services.extraction.relation import format_relation_types_desc
from services.extraction.prompts import (
    COMBINED_EXTRACTION_SYSTEM,
    COMBINED_EXTRACTION_USER,
)

logger = logging.getLogger(__name__)


async def extract_entities_and_relations(text: str, schema: Dict, stream_log: bool = False, extra_guidance: str = "") -> Dict[str, List[Dict]]:
    """
    一阶段合并抽取：同时获取实体和关系
    """
    entity_types_desc = format_entity_types_desc(schema)
    relation_types_desc = format_relation_types_desc(schema)

    system_content = COMBINED_EXTRACTION_SYSTEM.format(
        entity_types_desc=entity_types_desc,
        relation_types_desc=relation_types_desc
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
            "content": COMBINED_EXTRACTION_USER.format(text=text),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_NORMAL,
        stream_log=stream_log,
    )

    entities = result.get("entities", [])
    relations = result.get("relations", [])
    
    logger.info(f"合并抽取完成: {len(entities)} 实体, {len(relations)} 关系")
    
    return {
        "entities": entities,
        "relations": relations
    }
