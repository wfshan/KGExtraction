"""多阶段精炼流水线（Wikontic 模式）。

对已抽取的草稿图谱（候选）执行：
  Stage 1: 批次实体聚类对齐（KGGen）
  Stage 2: 关系谓词规范化（DIAL-KG / Wikontic）
  Stage 3: 后验本体批量修正（OAK+MEND）
  Stage 4: 确定性 Schema 校验报告（CES 执行层，仅报告不落库）

将「高召回候选 → 对齐/精炼 → 校验」串成可治理中间路线。
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


async def run_fusion_pipeline(
    project_id: str,
    do_entity_clustering: bool = True,
    do_relation_canonicalize: bool = True,
    do_post_correction: bool = True,
    use_llm: bool = True,
) -> Dict:
    """对草稿图谱执行多阶段精炼流水线，返回各阶段统计。"""
    from services.fusion.entity_clustering import cluster_entities
    from services.fusion.relation_canonicalize import canonicalize_relations
    from services.extraction.post_correction import post_extraction_correction
    from services.graph_store import get_publish_validation_report

    report: Dict[str, Dict] = {}

    if do_entity_clustering:
        logger.info("[融合流水线] Stage 1: 批次实体聚类对齐")
        report["entity_clustering"] = await cluster_entities(project_id, use_llm=use_llm)

    if do_relation_canonicalize:
        logger.info("[融合流水线] Stage 2: 关系谓词规范化")
        report["relation_canonicalize"] = await canonicalize_relations(project_id)

    if do_post_correction:
        logger.info("[融合流水线] Stage 3: 后验本体批量修正")
        report["post_correction"] = await post_extraction_correction(project_id)

    logger.info("[融合流水线] Stage 4: 确定性 Schema 校验报告")
    report["validation"] = get_publish_validation_report(project_id)

    return report
