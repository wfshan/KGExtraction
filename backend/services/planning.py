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
    return _compile_plan(schema, version, config)


def _compile_plan(schema: Dict, version: int, config) -> Plan:
    """基于给定 schema dict 编译一份 Plan（供 compile_default_plan 与规划预览复用）。"""
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

    # inductive 分道：从案例归纳抽象知识 + 结构校验 + 忠实度校验（v3 第②步+深化）
    if inductive_types:
        add("s_induce", Primitive.induce_from_cases,
            f"从案例归纳抽象知识（类型: {', '.join(inductive_types)}）",
            depends_on=["s_segment"], targets=inductive_types)
        add("s_struct", Primitive.validate_structure,
            "结构校验：剔除缺必填结构字段的残缺/空泛归纳（确定性）",
            depends_on=["s_induce"], targets=inductive_types)
        add("s_faithful", Primitive.verify_faithfulness,
            "归纳忠实度校验：剔除不被源案例支撑的归纳（挡幻觉归纳）",
            depends_on=["s_struct"], targets=inductive_types)

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


# ==========================================================================
# 规划器（Planner，v3 第③步）
#
# 设计：LLM 只做「语义标注」（判断每个类型是表面还是归纳、需要什么结构字段），
# DAG 编排交给确定性的 _compile_plan。这既发挥 LLM 长处，又守住契约 D 的三条
# 不变量（封闭性/可校验/可解释）——LLM 不发明步骤，只决定类型的抽取语义。
# ==========================================================================

SUGGEST_SEMANTICS_PROMPT = """你是知识抽取流程规划专家。给定本体的实体类型定义与文档样本，
为每个类型判断其「抽取语义」，以决定它该走表面抽取还是归纳抽取。

## 判断维度 abstractness
- surface：实例在文本中表面存在、可直接定位（人名、机构名、文件名、账号、术语）
- normalized：表面存在但需标准化（日期→ISO、金额→数值、比例）
- inductive：实例不在原文中、需从案例/描述**归纳概括**（规则、模式、概念、结论、要点）

## 若判为 inductive，请给出 structure_template
即该类抽象知识应包含哪些结构字段（如「触发条件」「风险类别」「适用场景」），
其中「可判别条件」类字段应标 required=true。

## 实体类型（含定义）
{types}

## 文档样本
{samples}

## 用户意图（可空）
{user_intent}

## 输出（严格 JSON）
{{
  "semantics": [
    {{
      "name": "类型名（须来自上方列表）",
      "abstractness": "surface | normalized | inductive",
      "structure_template": {{"fields": [{{"key": "字段名", "required": true, "description": "说明"}}]}},
      "reason": "判断依据（一句话）"
    }}
  ]
}}
"""


def _evidence_mode_for(abstractness: str) -> str:
    """证据模式由抽象度确定性推导：归纳走 span，其余走 verbatim。"""
    return "span" if abstractness == "inductive" else "verbatim"


def _sample_chunks(project_id: str, n: int = 8) -> List[str]:
    try:
        from services.chunk_store import list_all_chunks
        chunks = list_all_chunks(project_id)
    except Exception:
        return []
    if not chunks:
        return []
    if len(chunks) <= n:
        return [c.get("content", "") for c in chunks]
    step = len(chunks) / n
    return [chunks[int(i * step)].get("content", "") for i in range(n)]


async def suggest_extraction_semantics(project_id: str, user_intent: str = "") -> List[Dict]:
    """LLM 为每个实体类型建议抽取语义（abstractness / structure_template / 理由）。

    未被 LLM 覆盖的类型沿用其现有设置。返回列表供前端确认与编辑。
    """
    import json

    schema, _ = _load_schema_and_version(project_id)
    ets = schema.get("entity_types", [])
    if not ets:
        return []

    samples = _sample_chunks(project_id)
    types_desc = json.dumps(
        [{"name": e.get("name"), "definition": e.get("definition", "")} for e in ets],
        ensure_ascii=False, indent=2,
    )
    samples_text = "\n---\n".join(s[:600] for s in samples if s) or "（无文档样本，请仅依据类型定义判断）"

    valid_names = {e.get("name") for e in ets}
    suggestions: Dict[str, Dict] = {}
    try:
        from services.llm_gateway import llm_gateway, COMPLEXITY_COMPLEX
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是知识抽取流程规划专家，只返回 JSON。"},
                {"role": "user", "content": SUGGEST_SEMANTICS_PROMPT.format(
                    types=types_desc, samples=samples_text, user_intent=user_intent or "（未提供）",
                )},
            ],
            complexity=COMPLEXITY_COMPLEX,
        )
        for s in result.get("semantics", []):
            name = s.get("name")
            if name not in valid_names:
                continue
            ab = s.get("abstractness", "surface")
            if ab not in ("surface", "normalized", "inductive"):
                ab = "surface"
            suggestions[name] = {
                "name": name,
                "abstractness": ab,
                "evidence_mode": _evidence_mode_for(ab),
                "structure_template": s.get("structure_template") if ab == "inductive" else None,
                "reason": s.get("reason", ""),
            }
    except Exception as e:
        logger.warning(f"[规划器] LLM 语义建议失败，回退现有设置: {e}")

    # 补齐未覆盖的类型（沿用现有 schema 设置）
    out: List[Dict] = []
    for e in ets:
        name = e.get("name")
        if name in suggestions:
            out.append(suggestions[name])
        else:
            ab = e.get("abstractness", "surface")
            out.append({
                "name": name,
                "abstractness": ab,
                "evidence_mode": _evidence_mode_for(ab),
                "structure_template": e.get("structure_template"),
                "reason": "（沿用现有设置）",
            })
    return out


def _apply_semantics_to_schema(schema: Dict, semantics: List[Dict]) -> Dict:
    """把 semantics 写入 schema dict 的实体类型（就地修改并返回）。"""
    sem_by_name = {s.get("name"): s for s in semantics}
    for et in schema.get("entity_types", []):
        s = sem_by_name.get(et.get("name"))
        if not s:
            continue
        ab = s.get("abstractness", "surface")
        et["abstractness"] = ab
        et["evidence_mode"] = s.get("evidence_mode") or _evidence_mode_for(ab)
        if s.get("structure_template") is not None:
            et["structure_template"] = s.get("structure_template")
    return schema


def compile_preview_plan(project_id: str, semantics: List[Dict], config=None) -> Plan:
    """用建议的 semantics 覆盖 schema 后编译预览 Plan（不落库）。"""
    if config is None:
        from config import load_config
        config = load_config()
    schema, version = _load_schema_and_version(project_id)
    schema = _apply_semantics_to_schema(schema, semantics)
    return _compile_plan(schema, version, config)


def apply_extraction_semantics(project_id: str, semantics: List[Dict]) -> Dict:
    """把 semantics 写回 schema.json（持久化抽取语义），返回更新后的 schema。"""
    import json
    from config import get_project_dir
    from services.graph_store import _load_schema_dict

    schema = _load_schema_dict(project_id) or {"entity_types": [], "relation_types": []}
    schema = _apply_semantics_to_schema(schema, semantics)
    path = get_project_dir(project_id) / "schema.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    return schema
