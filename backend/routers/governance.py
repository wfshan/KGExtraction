"""知识治理与演进路由。

聚合 2026 演进能力的操作入口：
- 融合：实体聚类、关系规范化、后验修正、多阶段流水线
- 社区：构建/读取社区摘要
- 评测：MINE-1 / FactExtract / 下游效用
- Schema 演化：缺口检测 / 诱导 / 版本化
- 反思案例库读取
"""
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.schema import SchemaConfig

logger = logging.getLogger(__name__)
router = APIRouter()


# ===================== 融合 / 修正 =====================

@router.post("/{project_id}/graph/post-correct")
async def post_correct(project_id: str):
    """OAK+MEND 后验本体批量修正（作用于草稿图谱）。"""
    from services.extraction.post_correction import post_extraction_correction
    try:
        return await post_extraction_correction(project_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/graph/canonicalize-relations")
async def canonicalize_relations_api(project_id: str):
    """关系谓词规范化（聚类 + LLM 裁决，作用于草稿图谱）。"""
    from services.fusion import canonicalize_relations
    try:
        return await canonicalize_relations(project_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class ClusterRequest(BaseModel):
    use_llm: bool = True


@router.post("/{project_id}/graph/cluster-entities")
async def cluster_entities_api(project_id: str, req: ClusterRequest = None):
    """KGGen 式批次实体聚类融合（作用于草稿图谱）。"""
    from services.fusion import cluster_entities
    req = req or ClusterRequest()
    try:
        return await cluster_entities(project_id, use_llm=req.use_llm)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/graph/merge-inductive")
async def merge_inductive_api(project_id: str):
    """归纳知识语义归并（v3）：合并同义的抽象规则/概念，置信度按支撑案例数客观化。"""
    from services.fusion import merge_inductive_knowledge
    try:
        return await merge_inductive_knowledge(project_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FuseRequest(BaseModel):
    do_entity_clustering: bool = True
    do_relation_canonicalize: bool = True
    do_post_correction: bool = True
    use_llm: bool = True


@router.post("/{project_id}/graph/fuse")
async def fuse_pipeline_api(project_id: str, req: FuseRequest = None):
    """Wikontic 式多阶段精炼流水线：聚类 → 规范化 → 后验修正 → 校验报告。"""
    from services.fusion import run_fusion_pipeline
    req = req or FuseRequest()
    try:
        return await run_fusion_pipeline(
            project_id,
            do_entity_clustering=req.do_entity_clustering,
            do_relation_canonicalize=req.do_relation_canonicalize,
            do_post_correction=req.do_post_correction,
            use_llm=req.use_llm,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===================== 社区摘要 =====================

@router.post("/{project_id}/graph/communities")
async def build_communities_api(project_id: str, min_size: int = 3):
    """在已发布图谱上构建 Leiden 社区摘要（Microsoft GraphRAG 模式）。"""
    from services.community import build_communities_async
    try:
        return await build_communities_async(project_id, min_size=min_size)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/graph/communities")
async def get_communities_api(project_id: str):
    """读取已构建的社区摘要。"""
    from services.community import load_communities
    return {"communities": load_communities(project_id)}


# ===================== 评测基线 =====================

@router.post("/{project_id}/benchmark/mine1")
async def benchmark_mine1_api(project_id: str, sample_size: int = 10, status: str = "published"):
    """MINE-1 信息保留率评测。"""
    from services.benchmark import run_mine1
    try:
        return await run_mine1(project_id, sample_size=sample_size, status=status)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class FactExtractRequest(BaseModel):
    gold_triples: List[Dict[str, str]]
    status: str = "published"


@router.post("/{project_id}/benchmark/factextract")
async def benchmark_factextract_api(project_id: str, req: FactExtractRequest):
    """FactExtract F1 评测（对比 gold 三元组）。"""
    from services.benchmark import run_factextract
    try:
        return run_factextract(project_id, req.gold_triples, status=req.status)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/benchmark/downstream-utility")
async def benchmark_downstream_api(project_id: str, sample_size: int = 5, retrieval_mode: str = "graph_flow"):
    """AutoGraph-R1 式下游 RAG 效用评估。"""
    from services.downstream_feedback import evaluate_graph_utility
    try:
        return await evaluate_graph_utility(project_id, sample_size=sample_size, retrieval_mode=retrieval_mode)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ===================== Schema 演化 =====================

@router.get("/{project_id}/schema/gaps")
async def schema_gaps_api(project_id: str):
    """检测草稿图谱相对当前 Schema 的类型缺口。"""
    from services.schema_evolution import detect_schema_gaps
    return detect_schema_gaps(project_id)


@router.post("/{project_id}/schema/induce")
async def schema_induce_api(project_id: str):
    """诱导 Schema 增补候选（不落库，供人工确认）。"""
    from services.schema_evolution import induce_schema_additions
    try:
        return await induce_schema_additions(project_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/schema/evolve/preview", response_model=SchemaConfig)
async def schema_evolve_preview_api(project_id: str):
    """诱导并合并出新版 Schema 预览（不落库）。"""
    from services.schema_evolution import induce_schema_additions, merge_additions_into_schema
    try:
        additions = await induce_schema_additions(project_id)
        return merge_additions_into_schema(project_id, additions)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class EvolveApplyRequest(BaseModel):
    schema_config: SchemaConfig
    note: str = ""


@router.post("/{project_id}/schema/evolve/apply")
async def schema_evolve_apply_api(project_id: str, req: EvolveApplyRequest):
    """人工确认后写入新版 Schema 并版本化快照。请求体: {schema_config, note}。"""
    from services.schema_evolution import apply_schema_version
    try:
        return apply_schema_version(project_id, req.schema_config, note=req.note)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/schema/versions")
async def schema_versions_api(project_id: str):
    """列出 Schema 历史版本。"""
    from services.schema_evolution import list_schema_versions
    return {"versions": list_schema_versions(project_id)}


# ===================== 反思案例库 =====================

@router.get("/{project_id}/reflection/cases")
async def reflection_cases_api(project_id: str, limit: int = 50):
    """读取人工复核反思案例库。"""
    from services.reflection import load_cases
    cases = load_cases(project_id)
    return {"total": len(cases), "cases": cases[-limit:]}
