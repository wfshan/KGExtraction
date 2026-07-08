"""抽取计划编译与解析（v3 第①步）。

本模块提供三件事：
1. compile_default_plan：把当前固定流水线「反编译」为一份等价的默认 Plan
   （忠实反映 config 开关与抽取模式），使执行路径开始「经过 Plan」。
2. plan_to_execution_params：从 Plan 解析回等价的 config 覆盖参数。
   与 compile 构成往返（round-trip）：config → plan → params 覆盖后与原 config 一致，
   这是「零行为变更」的保证。
3. validate_plan：Plan 的确定性校验（DAG 无环 + 依赖存在），契约 D validator 的雏形。

第①步执行器把 Plan 当作「结构化的抽取配置」解析回参数运行；真正的
「按 Plan 遍历原语执行」在第②步落地。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from models.plan import (
    Plan, Step, Primitive, KnowledgeTypeMeta, Abstractness, EvidenceMode,
)


def _load_schema_and_version(project_id: str) -> Tuple[Dict, int]:
    from services.graph_store import _load_schema_dict
    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    version = 0
    try:
        from services.schema_evolution import list_schema_versions
        vs = list_schema_versions(project_id)
        if vs:
            version = max(v.get("version", 0) for v in vs)
    except Exception:
        pass
    return schema, version


def compile_default_plan(project_id: str, config=None) -> Plan:
    """把当前 config + Schema 反编译为等价的默认 Plan（source=planner-default）。

    每个 config 开关映射为「某原语步骤的有无」，抽取模式映射为
    extract_combined（one-pass）vs extract_surface+extract_relations_intra（multi-pass）。
    """
    if config is None:
        from config import load_config
        config = load_config()

    schema, version = _load_schema_and_version(project_id)

    # 契约 A：读取每个类型声明的抽象度（旧数据缺省 surface + verbatim）
    def _enum(cls, val, default):
        try:
            return cls(val)
        except Exception:
            return default

    knowledge_types: Dict[str, KnowledgeTypeMeta] = {}
    for et in schema.get("entity_types", []):
        name = et.get("name")
        if name:
            knowledge_types[name] = KnowledgeTypeMeta(
                name=name,
                abstractness=_enum(Abstractness, et.get("abstractness", "surface"), Abstractness.surface),
                evidence_mode=_enum(EvidenceMode, et.get("evidence_mode", "verbatim"), EvidenceMode.verbatim),
                identity_by=et.get("identity_by", "name") or "name",
                structure_template=et.get("structure_template"),
            )

    inductive_types = [n for n, kt in knowledge_types.items() if kt.abstractness == Abstractness.inductive]

    steps: List[Step] = []

    def add(step_id: str, primitive: Primitive, reason: str, depends_on=None, params=None, targets=None):
        steps.append(Step(
            step_id=step_id,
            primitive=primitive,
            targets=targets or [],
            depends_on=depends_on or [],
            params=params or {},
            reason=reason,
        ))

    # 分片（策略元信息；分片实际在文档上传时完成）
    add("s_segment", Primitive.segment,
        "文档分片策略（上传时已应用，Plan 中作为流程元信息记录）",
        params={"chunk_size": config.chunk_size, "chunk_overlap": config.chunk_overlap})

    # 抽取
    extraction_mode = getattr(config, "extraction_mode", "one-pass")
    if extraction_mode == "one-pass":
        add("s_extract", Primitive.extract_combined,
            "one-pass：单次合并抽取实体与关系", depends_on=["s_segment"])
        extract_dep = "s_extract"
    else:
        add("s_extract_e", Primitive.extract_surface,
            "multi-pass：先抽实体", depends_on=["s_segment"])
        add("s_extract_r", Primitive.extract_relations_intra,
            "multi-pass：再抽片段内关系", depends_on=["s_extract_e"])
        extract_dep = "s_extract_r"

    # inductive 分道：从案例归纳抽象知识 + 忠实度校验（v3 第②步）
    if inductive_types:
        add("s_induce", Primitive.induce_from_cases,
            f"从案例归纳抽象知识（类型: {', '.join(inductive_types)}）",
            depends_on=["s_segment"], targets=inductive_types)
        add("s_faithful", Primitive.verify_faithfulness,
            "归纳忠实度校验：剔除不被源案例支撑的归纳（挡幻觉归纳）",
            depends_on=["s_induce"], targets=inductive_types)

    # 证据锚定（现状为抽取内联的逐字校验）
    if getattr(config, "enable_evidence_anchor", True):
        add("s_evidence", Primitive.verify_evidence_verbatim,
            "证据锚定：抽取时要求原文短句并逐字校验命中（surface 分道）",
            depends_on=[extract_dep], targets=list(knowledge_types.keys()))

    # 实体消歧
    if getattr(config, "enable_disambiguation", True):
        add("s_resolve", Primitive.resolve_surface,
            "表面消歧：向量/名称召回 + LLM 裁决合并同一实体",
            depends_on=[extract_dep])

    # 类型合规校验（始终执行）
    add("s_validate_type", Primitive.validate_type,
        "确定性类型校验：实体/关系类型必须在 Schema 中", depends_on=[extract_dep])

    # 跨片段关系推断
    if getattr(config, "enable_cross_chunk_inference", False):
        add("s_cross", Primitive.infer_relations_cross,
            "跨片段关系推断（候选按与当前片段相关性排序）", depends_on=[extract_dep])

    # 逐片段自我修正
    if getattr(config, "enable_self_correction", False):
        add("s_self_correct", Primitive.self_correct,
            "v2 逐片段自我修正（LLM 检查类型/方向/重复）", depends_on=["s_validate_type"])

    # 后验本体批量修正
    if getattr(config, "enable_post_correction", False):
        add("s_post_correct", Primitive.post_correct,
            "OAK+MEND 后验本体批量修正（低 token 成本）", depends_on=["s_validate_type"])

    # 文档结构层（始终执行）
    add("s_doc_structure", Primitive.add_document_structure,
        "固化文档片段锚点与「下一段」边（文档结构层，与知识层分离）",
        depends_on=[extract_dep])

    plan = Plan(
        schema_version=version,
        source="planner-default",
        knowledge_types=knowledge_types,
        steps=steps,
    )
    ok, _ = validate_plan(plan)
    plan.validated = ok
    return plan


def plan_to_execution_params(plan: Plan) -> Dict[str, Any]:
    """从 Plan 解析回 config 覆盖参数（compile 的逆）。

    只返回 SystemConfig 中存在的字段，供 config.model_copy(update=...) 使用。
    """
    params: Dict[str, Any] = {}

    # 抽取模式：由抽取原语类型决定
    if plan.has_primitive(Primitive.extract_combined):
        params["extraction_mode"] = "one-pass"
    elif plan.has_primitive(Primitive.extract_surface):
        params["extraction_mode"] = "multi-pass"

    # 开关：由对应原语步骤的有无决定
    params["enable_evidence_anchor"] = plan.has_primitive(Primitive.verify_evidence_verbatim)
    params["enable_disambiguation"] = plan.has_primitive(Primitive.resolve_surface)
    params["enable_cross_chunk_inference"] = plan.has_primitive(Primitive.infer_relations_cross)
    params["enable_self_correction"] = plan.has_primitive(Primitive.self_correct)
    params["enable_post_correction"] = plan.has_primitive(Primitive.post_correct)

    # 分片参数：从 segment 步骤读回
    for s in plan.steps:
        if s.primitive == Primitive.segment:
            if "chunk_size" in s.params:
                params["chunk_size"] = s.params["chunk_size"]
            if "chunk_overlap" in s.params:
                params["chunk_overlap"] = s.params["chunk_overlap"]
            break

    return params


def validate_plan(plan: Plan) -> Tuple[bool, List[str]]:
    """确定性校验：step_id 唯一、depends_on 指向存在的步骤、DAG 无环。"""
    errors: List[str] = []
    ids = [s.step_id for s in plan.steps]
    id_set = set(ids)

    if len(ids) != len(id_set):
        errors.append("存在重复的 step_id")

    for s in plan.steps:
        for dep in s.depends_on:
            if dep not in id_set:
                errors.append(f"步骤 {s.step_id} 依赖了不存在的步骤 {dep}")

    # 环检测（Kahn 拓扑排序）
    from collections import deque
    indeg = {sid: 0 for sid in id_set}
    adj: Dict[str, List[str]] = {sid: [] for sid in id_set}
    for s in plan.steps:
        for dep in s.depends_on:
            if dep in id_set:
                adj[dep].append(s.step_id)
                indeg[s.step_id] += 1
    q = deque([sid for sid in id_set if indeg[sid] == 0])
    visited = 0
    while q:
        cur = q.popleft()
        visited += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
    if visited != len(id_set):
        errors.append("Plan 的 DAG 中存在环")

    return (len(errors) == 0, errors)
