"""
实体抽取与消歧模块
"""
import json
import logging
from typing import Any, Dict, List, Tuple

from models.graph import Node
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL, COMPLEXITY_COMPLEX
from services.extraction.prompts import (
    ENTITY_EXTRACTION_SYSTEM,
    ENTITY_EXTRACTION_USER,
    ENTITY_DISAMBIGUATION_SYSTEM,
    ENTITY_DISAMBIGUATION_USER,
)

logger = logging.getLogger(__name__)


def format_entity_types_desc(schema: Dict) -> str:
    """格式化实体类型描述，用于 Prompt"""
    lines = []
    for et in schema.get("entity_types", []):
        line = f"- **{et['name']}**: {et.get('definition', '无定义')}"
        if et.get("examples"):
            line += f"（示例: {', '.join(et['examples'])}）"
        lines.append(line)
    return "\n".join(lines) if lines else "（未定义实体类型，请自由抽取）"


async def extract_entities(text: str, schema: Dict, stream_log: bool = False, extra_guidance: str = "") -> List[Dict]:
    """
    从文本中抽取实体

    Args:
        text: 文本片段
        schema: Schema 配置

    Returns:
        实体列表 [{"name", "entity_type", "properties", "confidence"}]
    """
    entity_types_desc = format_entity_types_desc(schema)

    system_content = ENTITY_EXTRACTION_SYSTEM.format(entity_types_desc=entity_types_desc)
    if extra_guidance:
        system_content += extra_guidance

    messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": ENTITY_EXTRACTION_USER.format(text=text),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_NORMAL,
        stream_log=stream_log,
    )

    entities = result.get("entities", [])
    logger.info(f"从文本中抽取了 {len(entities)} 个实体")
    return entities


async def disambiguate_entities(
    new_entities: List[Dict],
    candidate_entities: List[Tuple[Dict, float]],
    context: str,
    stream_log: bool = False,
) -> List[Dict]:
    """
    实体消歧 - 判断新实体是否与已有实体相同

    Args:
        new_entities: 新抽取的实体列表
        candidate_entities: 向量召回的候选实体列表 [(metadata, score)]
        context: 当前文本上下文

    Returns:
        消歧决策列表
    """
    if not candidate_entities:
        # 没有候选，所有都是新实体
        return [
            {"new_entity_name": e["name"], "match_entity_id": None, "is_same": False}
            for e in new_entities
        ]

    # 格式化候选实体
    candidates_str = json.dumps(
        [{"id": c[0].get("node_id", ""), "name": c[0].get("name", ""), "type": c[0].get("entity_type", ""), "score": round(c[1], 3)} for c in candidate_entities],
        ensure_ascii=False,
        indent=2,
    )

    new_entities_str = json.dumps(
        [{"name": e["name"], "entity_type": e["entity_type"]} for e in new_entities],
        ensure_ascii=False,
        indent=2,
    )

    messages = [
        {"role": "system", "content": ENTITY_DISAMBIGUATION_SYSTEM},
        {
            "role": "user",
            "content": ENTITY_DISAMBIGUATION_USER.format(
                new_entities=new_entities_str,
                candidate_entities=candidates_str,
                context=context[:500],
            ),
        },
    ]

    result = await llm_gateway.chat_json(
        messages=messages,
        complexity=COMPLEXITY_COMPLEX,
        stream_log=stream_log,
    )

    decisions = result.get("decisions", [])
    logger.info(f"消歧完成: {sum(1 for d in decisions if d.get('is_same'))} 个实体已匹配")
    return decisions
