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
)

logger = logging.getLogger(__name__)


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
