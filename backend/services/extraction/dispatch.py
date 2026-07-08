"""候选生成分派器（v3 第②步）。

按知识类型的抽象度把「从片段生成候选」这一环节分流：
- surface / normalized 类型 → 现有表面抽取（extract_combined / multi-pass），逻辑不变；
- inductive 类型         → 归纳抽取（induce_from_cases + verify_faithfulness）。

关键不变量：当项目中没有任何 inductive 类型时，surface 分支使用的 schema 与
原 chunk_schema 完全一致，调用路径等价于现状 —— 即 surface-only 零回归。
这是「增量打通 inductive、不动 surface」的核心保证。
"""
import logging
from typing import Any, Dict, List, Optional

from services.extraction.combined import extract_entities_and_relations
from services.extraction.entity import extract_entities
from services.extraction.induction import induce_from_cases, verify_faithfulness, validate_structure

logger = logging.getLogger(__name__)


def _abstractness_of(type_name: str, plan) -> str:
    if plan is None:
        return "surface"
    meta = plan.knowledge_types.get(type_name)
    if meta is None:
        return "surface"
    val = getattr(meta.abstractness, "value", meta.abstractness)
    return str(val)


def split_entity_types(chunk_schema: Dict, plan) -> tuple[List[Dict], List[Dict]]:
    """把 chunk_schema 的实体类型按抽象度分为 (surface_like, inductive)。

    surface_like 包含 surface 与 normalized（normalized 第②步暂与 surface 同路，
    其值标准化留待后续 normalize_value 原语）。
    """
    surface_like, inductive = [], []
    for et in chunk_schema.get("entity_types", []):
        if _abstractness_of(et.get("name", ""), plan) == "inductive":
            inductive.append(et)
        else:
            surface_like.append(et)
    return surface_like, inductive


async def generate_candidates(
    chunk_text: str,
    chunk_schema: Dict,
    plan,
    config,
    reflection_hint: str = "",
    stream_log: bool = False,
) -> Dict[str, Any]:
    """返回 {entities, relations, rejected_inductive}。

    entities 合并 surface 抽取与（通过忠实度校验的）归纳知识；归纳项带
    `_abstractness="inductive"` 标记，供写图时证据分道。
    """
    surface_ets, inductive_ets = split_entity_types(chunk_schema, plan)

    entities: List[Dict] = []
    relations: List[Dict] = []
    rejected_inductive: List[Dict] = []

    # ---- surface 分道（等价现状）----
    # 无 inductive 类型时 surface_ets == 全部实体类型，surface_schema == chunk_schema。
    run_surface = bool(surface_ets) or not inductive_ets
    if run_surface:
        surface_schema = {
            "entity_types": surface_ets,
            "relation_types": chunk_schema.get("relation_types", []),
        }
        if getattr(config, "extraction_mode", "one-pass") == "one-pass":
            r = await extract_entities_and_relations(
                chunk_text, surface_schema, stream_log=stream_log, extra_guidance=reflection_hint,
            )
            entities.extend(r.get("entities", []))
            relations.extend(r.get("relations", []))
        else:
            ents = await extract_entities(
                chunk_text, surface_schema, stream_log=stream_log, extra_guidance=reflection_hint,
            )
            entities.extend(ents)
            # multi-pass 的关系仍由 graph.py 后续 extract_relations 段处理（保持现状）

    # ---- inductive 分道（新增能力）----
    if inductive_ets:
        induced = await induce_from_cases(
            chunk_text, inductive_ets, stream_log=stream_log, extra_guidance=reflection_hint,
        )
        if induced:
            # 关1：结构校验（确定性）——挡残缺/空泛规则
            induced, struct_rejected = validate_structure(induced, inductive_ets)
            for it in struct_rejected:
                rejected_inductive.append({
                    "name": it.get("name", ""),
                    "entity_type": it.get("entity_type", ""),
                    "reason": it.get("_reject_reason", "invalid_structure"),
                    "payload": {k: v for k, v in it.items() if not k.startswith("_")},
                })
            # 关2：忠实度校验（LLM）——挡幻觉归纳
            if induced:
                verdict = await verify_faithfulness(chunk_text, induced, stream_log=stream_log)
                for it in induced:
                    if verdict.get(it.get("name", ""), True):
                        entities.append(it)
                    else:
                        rejected_inductive.append({
                            "name": it.get("name", ""),
                            "entity_type": it.get("entity_type", ""),
                            "reason": "unfaithful_induction",
                            "payload": {k: v for k, v in it.items() if not k.startswith("_")},
                        })
            logger.info(f"归纳分道：拒绝 {len(rejected_inductive)} 条（结构+忠实度双关）")

    return {"entities": entities, "relations": relations, "rejected_inductive": rejected_inductive}
