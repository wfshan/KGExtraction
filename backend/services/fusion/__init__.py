"""知识融合层（Wikontic / KGGen / DIAL-KG 模式）。

包含：
- relation_canonicalize：关系谓词规范化（聚类 + LLM 裁决）
- entity_clustering：批次实体聚类融合
- pipeline：多阶段精炼流水线（候选 → 对齐/聚类 → 规范化 → 校验）
"""
from services.fusion.relation_canonicalize import canonicalize_relations
from services.fusion.entity_clustering import cluster_entities
from services.fusion.pipeline import run_fusion_pipeline

__all__ = [
    "canonicalize_relations",
    "cluster_entities",
    "run_fusion_pipeline",
]
