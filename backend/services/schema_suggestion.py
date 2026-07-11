"""
Schema 智能建议服务
根据文档内容自动生成 Schema (实体类型 + 关系类型) 建议
"""
import json
import logging
from typing import Dict, List
from models.schema import SchemaConfig, EntityType, RelationType
from services.llm_gateway import llm_gateway, COMPLEXITY_COMPLEX, COMPLEXITY_NORMAL
from services.extraction_logger import log_extraction

logger = logging.getLogger(__name__)

DOC_PROFILING_PROMPT = """你是一位从本体论（Ontology）与知识结构视角分析文档的领域架构师。你的分析将直接用于：① 指导该领域本体（Schema）的设计；② 支撑后续以知识图谱为载体的多跳推理与溯源（GraphRAG），而非单纯检索文本片段。

请基于以下来自目标文档的均匀采样片段（全文共 {total_chunks} 个块），输出一份【文档宏观图谱特征分析报告】。

## 采样文档片段：
{text_samples}

## 分析维度（站在“知识如何被结构化与推理”的层面回答）：
1. **领域边界与核心命题**：这份材料在谈什么本质问题？其所属领域与文档类型（技术报告、学术论文、法律、说明书、叙事等）如何？这决定了后续图谱的论域与推理边界。
2. **结构化特征**：知识呈现方式——叙事线、因果链、指标体系、流程节点等——以便判断实体与关系应如何组织。
3. **具体实体与抽象概念**：区分可指称的对象（人、物、机构、地点、产品等）与作为“状态/度量/风险/过程”的抽象概念；二者在本体中的角色不同，关系类型也不同。
4. **关系类型的语义本质**：对象与概念之间是因果、归属、时序、度量、依赖还是其他？明确关系的语义类型，为 Schema 中关系设计提供哲学层面的约束。
5. **潜在实体间交互**：具体对象之间、具体对象与抽象概念之间，存在哪些核心连接与作用，便于后续多跳展开与溯源。

请用 Markdown 直接输出报告，不要客套话。
"""

SUGGEST_PROMPT = """你是一个知识图谱专家。请基于【全局领域概括】、【原始文档片段】以及【结构化文件表头】，生成一套能全面覆盖这些维度的 Schema 建议。

## 第一部分：全局领域概括 (Document Profile)
{document_profile}

## 第二部分：原始文档片段样本
{text_samples}

## 第三部分：结构化文件表头（若有；列名是天然的候选实体/属性信号）
{structured_headers}

## Schema 设计要求：
1. **实体类型 (Entity Types)**：
   - 涵盖具体实体，也要包含能反映深层业务逻辑的抽象概念（规则、模式、结论等）。
   - 结构化文件的表头列，优先映射为实体或其属性。
   - 数量不设严苛上限，尽可能全面覆盖核心实体。
2. **为每个实体类型标注抽取抽象度 `abstractness`**（决定它走哪条抽取子流程）：
   - `surface`：文本中可直接定位的实体（人名、机构、账号、文件名）。
   - `normalized`：表面存在但需标准化的值（日期→ISO、金额→数值、比例）。
   - `inductive`：原文不存在、需从案例/描述归纳的抽象知识（规则、模式、概念、结论）。
   - 仅当 `abstractness=inductive` 时，给出 `structure_template`（该类归纳知识应含哪些结构字段，可判别条件类字段标 required=true）。
3. **关系类型 (Relation Types)**：提取实体间核心关联；标明 source/target，确保它们存在于实体类型中。
4. 为每个类型提供简明定义和 1-2 个真实示例。

## 请严格以以下 JSON 格式输出：
{{
  "entity_types": [
    {{
      "name": "实体名称",
      "definition": "定义",
      "examples": ["示例1"],
      "abstractness": "surface | normalized | inductive",
      "structure_template": {{"fields": [{{"key": "字段名", "required": true, "description": "说明"}}]}},
      "color": "#4A90D9"
    }}
  ],
  "relation_types": [
    {{
      "name": "关系名称",
      "definition": "定义",
      "source_entity_type": "源类型",
      "target_entity_type": "目标类型",
      "examples": ["示例"]
    }}
  ]
}}
"""

SCHEMA_CHAT_SYS_PROMPT = """你是一位基于 GraphRAG 思想进行领域本体（Ontology）设计的架构专家。你的目标不是罗列名词，而是帮助用户建立一套“可支撑多跳推理与溯源”的本体结构：图谱中的实体与关系将作为问答时检索与展开的起点，并绑定原文片段作为依据。

系统已提供待处理文档的宏观分析报告，作为对话的认知背景。

## 待分析文档背景报告：
{document_profile}

## 你的任务与要求：
1. 从知识结构与推理依据的角度，与用户共同梳理：文档中的关键“具体实体”与“抽象概念”应如何映射为实体类型；对象与概念之间的“关系”应如何抽象为关系类型（因果、归属、时序、度量等）。
2. 以简明、友好的口吻交流；在合理范围内推断更多领域专有概念与拓扑关系，使本体足以覆盖后续图谱化与多跳问答的需求。
3. 若已达成共识，可总结当前的实体与关系设计，便于生成正式 Schema。

注意：以自然语言或 Markdown 列表交谈即可，不必直接输出 JSON。
"""

# 将文档宏观分析报告转写为对话开场白（精炼自然的一段话，用于 Drawer 打开时自动播报）
PROFILE_OPENING_PROMPT = """请将以下【文档宏观图谱特征分析报告】改写为一段精炼自然的开场白（约 150–250 字），直接面向用户口吻。

要求：
1. 开篇点明这份文档的核心命题、所属领域与文档类型，建立“论域”感。
2. 简要概括其中关键实体与关系的类型倾向（具体对象与抽象概念、以及关系语义），为后续本体设计做铺垫。
3. 自然过渡到邀请用户一起设计或微调本体（Schema），以便将文档知识以图谱形式组织，并用于多跳推理与溯源式问答。

不要复述报告全文，不用“根据报告”“如下所示”等套话；用连贯、友好的口语化表达。直接输出开场白正文，不要标题或前缀。"""

GENERATE_FROM_CHAT_PROMPT = """你是一个知识图谱专家。请仔细阅读下面提供的讨论 Schema 结构的对话历史。将其提取出来并规范化为 JSON Schema 格式。

要求：
1. 为每个实体类型提供简明定义和示例，并分配颜色。
2. 关系需标出所属于的源和目标实体。

## 会话内容：
{chat_history}

## 请严格以以下 JSON 格式输出：
{{
  "entity_types": [
    {{
      "name": "实体名称",
      "definition": "定义",
      "examples": ["示例"],
      "color": "#颜色"
    }}
  ],
  "relation_types": [
    {{
      "name": "关系名称",
      "definition": "定义",
      "source_entity_type": "源类型",
      "target_entity_type": "目标类型",
      "examples": ["示例"]
    }}
  ]
}}
"""

# 预设颜色列表
COLORS = [
    "#4A90D9", "#50C878", "#FF6B6B", "#FFD93D",
    "#9B59B6", "#1ABC9C", "#E67E22", "#3498DB",
    "#E91E63", "#00BCD4", "#8BC34A", "#FF9800",
]


def _build_relation_types(raw_relations, entity_types, normalize_examples) -> "List[RelationType]":
    """从 LLM 输出构造关系类型，并保证两端类型落在实体类型集合内。

    越界的一端降级为「不限」（空串）而非丢弃整条关系——这样建议出的 Schema 自洽，
    也能通过保存时的一致性校验，不会产生永远命不中的悬空关系。
    """
    entity_name_set = {et.name for et in entity_types}
    relation_types: List[RelationType] = []
    for rt in raw_relations or []:
        src = rt.get("source_entity_type", "") or ""
        tgt = rt.get("target_entity_type", "") or ""
        if src and src not in entity_name_set:
            src = ""
        if tgt and tgt not in entity_name_set:
            tgt = ""
        relation_types.append(RelationType(
            name=rt.get("name", ""),
            definition=rt.get("definition", ""),
            source_entity_type=src,
            target_entity_type=tgt,
            examples=normalize_examples(rt.get("examples", [])),
        ))
    return relation_types


async def get_or_generate_profile(project_id: str, force: bool = False) -> str:
    """获取或生成全局领域概括（Map-Reduce，见 services/profiling.py）。返回 Markdown。"""
    from services.profiling import map_reduce_profile
    log_extraction("=== 前置阶段：分层抽样 + Map-Reduce 领域概括 ===")
    global_md, _analyses, _headers = await map_reduce_profile(project_id, force=force)
    return global_md


def _format_structured_headers(structured_headers: Dict[str, List[str]]) -> str:
    if not structured_headers:
        return "（无结构化文件）"
    return "\n".join(f"- {fn}：{', '.join(cols)}" for fn, cols in structured_headers.items())


async def generate_schema_suggestion(project_id: str, sample_size: int = 18) -> SchemaConfig:
    """根据输入材料生成 Schema 建议（含抽象度标注）。内部完成分层抽样与领域概括。"""
    from services.profiling import stratified_sample, map_reduce_profile

    grouped, flat, total = stratified_sample(project_id, sample_size)

    # 冷启动项目（无分片数据）→ 从已导入图谱反推
    if total == 0:
        logger.info("Cold-start project detected. Extracting schema from existing graph data...")
        return await extract_schema_from_graph_data(project_id)

    combined = "\n\n---\n\n".join(flat)

    try:
        # 第一阶段：Map-Reduce 领域概括 + 结构化表头
        global_md, _analyses, structured_headers = await map_reduce_profile(project_id, budget=sample_size)

        # 第二阶段：本体设计生成（含抽象度）
        logger.info("Executing Stage 2: Schema Ontology Design...")
        log_extraction("\n=== 本体设计阶段：具体图谱本体结构 (JSON) 评估映射 ===")
        schema_messages = [
            {"role": "system", "content": "你是一个资深的知识图谱系统专家，你的任务是根据领域概括给出严谨且可用于图谱落地的 JSON Schema，并为每个实体类型标注抽取抽象度。"},
            {"role": "user", "content": SUGGEST_PROMPT.format(
                document_profile=global_md,
                text_samples=combined,
                structured_headers=_format_structured_headers(structured_headers),
            )},
        ]

        result = await llm_gateway.chat_json(
            messages=schema_messages,
            complexity=COMPLEXITY_COMPLEX,
            print_stream=True,
            stream_log=True,
        )

        # 解析结果（LLM 可能返回数字等，统一转为字符串列表）
        def _normalize_examples(raw):
            if isinstance(raw, str):
                return [raw]
            if not isinstance(raw, list):
                return []
            return [str(x) for x in raw]

        def _norm_abstractness(v):
            return v if v in ("surface", "normalized", "inductive") else "surface"

        entity_types = []
        for i, et in enumerate(result.get("entity_types", [])):
            examples = _normalize_examples(et.get("examples", []))
            ab = _norm_abstractness(et.get("abstractness", "surface"))
            entity_types.append(EntityType(
                name=et.get("name", f"Entity_{i}"),
                definition=et.get("definition", ""),
                examples=examples,
                color=et.get("color", COLORS[i % len(COLORS)]),
                abstractness=ab,
                evidence_mode="span" if ab == "inductive" else "verbatim",
                structure_template=et.get("structure_template") if ab == "inductive" else None,
            ))

        relation_types = _build_relation_types(
            result.get("relation_types", []), entity_types, _normalize_examples
        )

        return SchemaConfig(
            entity_types=entity_types,
            relation_types=relation_types,
        )

    except Exception as e:
        logger.error(f"Schema 建议生成失败: {e}")
        # 返回默认 Schema
        return SchemaConfig(
            entity_types=[
                EntityType(name="人物", definition="自然人", examples=["张三"], color=COLORS[0]),
                EntityType(name="组织", definition="机构或组织", examples=["公司A"], color=COLORS[1]),
                EntityType(name="地点", definition="地理位置", examples=["北京"], color=COLORS[2]),
            ],
            relation_types=[
                RelationType(name="就职于", definition="人物在组织工作", source_entity_type="人物", target_entity_type="组织"),
                RelationType(name="位于", definition="实体的地理位置", source_entity_type="组织", target_entity_type="地点"),
            ],
        )

async def stream_profile_summary(project_id: str, source: str = "documents"):
    """流式返回文档开场白（仅用于对话配置 Drawer 打开时自动播报，无需用户输入）。"""
    from config import get_project_dir

    if source == "graph":
        # 基于图谱数据生成开场白
        from services.graph_store import load_draft_graph
        graph = load_draft_graph(project_id)
        if not graph.nodes:
            yield "当前项目暂无图谱数据，请先导入 JSON 数据。"
            return
        entity_types = sorted(list(set(n.entity_type for n in graph.nodes)))
        relation_types = sorted(list(set(e.relation_type for e in graph.edges)))
        sample_nodes = [f"{n.name}({n.entity_type})" for n in graph.nodes[:20]]
        graph_desc = f"图谱包含 {len(graph.nodes)} 个节点、{len(graph.edges)} 条边。\n实体类型: {', '.join(entity_types)}\n关系类型: {', '.join(relation_types)}\n示例节点: {', '.join(sample_nodes)}"
        sys_prompt = "你是一个面向用户的领域本体设计向导，用简洁、自然的语言介绍已导入的图谱数据并引导用户参与 Schema 设计。只输出开场白正文，不要任何前缀或标题。"
        user_content = f"请根据以下已导入的图谱数据概况，生成一段精炼自然的开场白（约 150-250 字），点明数据的领域和内容特征，并邀请用户一起设计本体 Schema。\n\n{graph_desc}"
        msgs = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ]
        async for chunk in llm_gateway.chat_stream(messages=msgs, complexity=COMPLEXITY_COMPLEX):
            yield chunk
        return

    # documents 流程
    profile_path = get_project_dir(project_id) / "schema_profile.md"
    from services.profiling import stratified_sample
    _grouped, flat, total = stratified_sample(project_id, 18)
    if total == 0:
        yield "当前项目暂无文档片段，请先上传并解析文档。"
        return
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = f.read()
    else:
        yield "🔍 正在对项目文档进行分层抽样与领域概括（Map-Reduce），请稍候... (可能需要 30-60 秒)\n\n"
        profile = await get_or_generate_profile(project_id)
        yield "✅ 领域概括完成，正在生成本体设计开场白...\n\n---\n\n"
    sys_prompt = "你是一个面向用户的领域本体设计向导，用简洁、自然的语言介绍文档并引导用户参与 Schema 设计。只输出开场白正文，不要任何前缀或标题。"
    user_content = PROFILE_OPENING_PROMPT + "\n\n## 文档宏观图谱特征分析报告：\n" + profile
    msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content},
    ]
    async for chunk in llm_gateway.chat_stream(messages=msgs, complexity=COMPLEXITY_COMPLEX):
        yield chunk


async def chat_schema_stream(project_id: str, messages: List[dict], source: str = "documents"):
    """结合历史消息与背景领域概括，流式返回大模型的回答"""
    from config import get_project_dir
    import os

    if source == "graph":
        # 基于图谱数据的对话上下文
        from services.graph_store import load_draft_graph
        graph = load_draft_graph(project_id)
        entity_types = sorted(list(set(n.entity_type for n in graph.nodes)))
        relation_types = sorted(list(set(e.relation_type for e in graph.edges)))
        sample_nodes = [f"{n.name}({n.entity_type})" for n in graph.nodes[:30]]
        graph_profile = f"已导入图谱包含 {len(graph.nodes)} 个节点、{len(graph.edges)} 条边。\n实体类型: {', '.join(entity_types)}\n关系类型: {', '.join(relation_types)}\n示例节点: {', '.join(sample_nodes)}"
        sys_prompt = SCHEMA_CHAT_SYS_PROMPT.format(document_profile=graph_profile)
    else:
        profile_path = get_project_dir(project_id) / "schema_profile.md"
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = f.read()
        else:
            yield "🔍 背景报告不存在，正在为您进行领域概括...\n\n"
            profile = await get_or_generate_profile(project_id)
            yield "✅ 概括完成，正在处理您的提问...\n\n---\n\n"
        sys_prompt = SCHEMA_CHAT_SYS_PROMPT.format(document_profile=profile)

    msgs = [{"role": "system", "content": sys_prompt}]
    
    # 若为初次提问，则播报背景
    if len(messages) == 1:
        data_type = "图谱数据" if source == "graph" else "文档"
        msgs[0]["content"] += f"\n\n【重要执行指令】：当前是用户的初次对话。\n**在回应用户的具体提问之前，务必先用精炼自然的一段话，向用户总结这份{data_type}的核心内容、所属领域等作为开场白报告，然后再指导后续的本体结合设计与确认。注意口吻连贯。**"

    msgs.extend(messages)
    
    async for chunk in llm_gateway.chat_stream(
        messages=msgs,
        complexity=COMPLEXITY_COMPLEX,
    ):
        yield chunk

async def generate_schema_from_chat(messages: List[dict]) -> SchemaConfig:
    """根据多轮聊天历史提取并生成合规的 SchemaConfig"""
    # 抽取最近的对话（为了防止超出长度，可以取最后 10 条）
    recent_msgs = messages[-10:]
    chat_text = "\n".join([f"{m['role']}: {m['content']}" for m in recent_msgs])
    
    msgs = [
        {"role": "system", "content": "你是一个严格的图谱工程助手，只输出符合格式要求的 JSON。"},
        {"role": "user", "content": GENERATE_FROM_CHAT_PROMPT.format(chat_history=chat_text)}
    ]
    
    try:
        log_extraction("=== 根据多轮对话自动生成合并 Schema ===")
        result = await llm_gateway.chat_json(
            messages=msgs, 
            complexity=COMPLEXITY_COMPLEX,
            stream_log=True
        )
        
        def _normalize_examples(raw):
            if isinstance(raw, str):
                return [raw]
            if not isinstance(raw, list):
                return []
            return [str(x) for x in raw]

        entity_types = []
        for i, et in enumerate(result.get("entity_types", [])):
            examples = _normalize_examples(et.get("examples", []))
            ab = et.get("abstractness", "surface")
            if ab not in ("surface", "normalized", "inductive"):
                ab = "surface"
            entity_types.append(EntityType(
                name=et.get("name", f"Entity_{i}"),
                definition=et.get("definition", ""),
                examples=examples,
                color=et.get("color", COLORS[i % len(COLORS)]),
                abstractness=ab,
                evidence_mode="span" if ab == "inductive" else "verbatim",
                structure_template=et.get("structure_template") if ab == "inductive" else None,
            ))

        relation_types = _build_relation_types(
            result.get("relation_types", []), entity_types, _normalize_examples
        )

        return SchemaConfig(
            entity_types=entity_types,
            relation_types=relation_types,
        )

    except Exception as e:
        return SchemaConfig()

async def extract_schema_from_graph_data(project_id: str) -> SchemaConfig:
    """
    从已有图谱数据中提取 Schema：
    - 类型名、样例、关系两端类型：确定性提取
    - definition：单次 LLM 批量总结（保证抽取质量）
    """
    from collections import Counter, defaultdict
    from services.graph_store import load_draft_graph

    log_extraction("=== 图谱源 Schema 建议：开始 ===")

    # Step 1: 读取图谱（优先 draft，空则 published）
    log_extraction("[Step 1/6] 加载草稿图谱...")
    graph = load_draft_graph(project_id)
    if not graph.nodes:
        log_extraction("[Step 1/6] 草稿为空，加载已发布图谱...")
        from services.graph_store import load_published_graph
        graph = load_published_graph(project_id)

    if not graph.nodes:
        log_extraction("[Step 1/6] 未检测到图谱节点，返回空 Schema", "WARNING")
        return SchemaConfig()

    log_extraction(f"[Step 1/6] 图谱加载完成：nodes={len(graph.nodes)}, edges={len(graph.edges)}")

    # Step 2: 构建节点索引与实体类型样例
    log_extraction("[Step 2/6] 聚合实体类型与样例...")
    node_by_id = {n.id: n for n in graph.nodes}
    entity_samples = defaultdict(list)
    for n in graph.nodes:
        et = (n.entity_type or "").strip()
        if not et:
            continue
        if len(entity_samples[et]) < 5 and n.name:
            entity_samples[et].append(n.name)

    entity_types_found = sorted(entity_samples.keys())
    log_extraction(f"[Step 2/6] 发现实体类型 {len(entity_types_found)} 个")

    # Step 3: 聚合关系类型与两端类型分布
    log_extraction("[Step 3/6] 聚合关系类型与两端类型分布...")
    relation_pair_counter = defaultdict(Counter)   # relation_type -> Counter((src_type, tgt_type))
    relation_examples = defaultdict(list)          # relation_type -> ["A --(R)--> B"]

    for e in graph.edges:
        rt = (e.relation_type or "").strip()
        if not rt:
            continue
        s_node = node_by_id.get(e.source_id)
        t_node = node_by_id.get(e.target_id)
        src_type = (s_node.entity_type if s_node else "") or ""
        tgt_type = (t_node.entity_type if t_node else "") or ""
        if src_type or tgt_type:
            relation_pair_counter[rt][(src_type, tgt_type)] += 1
        if len(relation_examples[rt]) < 5:
            s_name = s_node.name if s_node else e.source_id
            t_name = t_node.name if t_node else e.target_id
            relation_examples[rt].append(f"{s_name} --({rt})--> {t_name}")

    relation_types_found = sorted(relation_examples.keys())
    log_extraction(f"[Step 3/6] 发现关系类型 {len(relation_types_found)} 个")

    # Step 4: 准备给 LLM 的类型摘要（单次批量请求）
    log_extraction("[Step 4/7] 构建定义总结输入（单次 LLM）...")
    relation_major_pairs: Dict[str, tuple[str, str]] = {}
    for rt in relation_types_found:
        pair_counter = relation_pair_counter.get(rt)
        src_type = ""
        tgt_type = ""
        if pair_counter:
            (src_type, tgt_type), _ = pair_counter.most_common(1)[0]
        relation_major_pairs[rt] = (src_type, tgt_type)

    llm_input = {
        "entity_types": [
            {
                "name": et,
                "examples": entity_samples.get(et, [])[:3],
            }
            for et in entity_types_found
        ],
        "relation_types": [
            {
                "name": rt,
                "source_entity_type": relation_major_pairs.get(rt, ("", ""))[0],
                "target_entity_type": relation_major_pairs.get(rt, ("", ""))[1],
                "examples": relation_examples.get(rt, [])[:3],
            }
            for rt in relation_types_found
        ],
    }

    llm_defs_by_entity: Dict[str, str] = {}
    llm_defs_by_relation: Dict[str, Dict[str, str]] = {}
    try:
        log_extraction("[Step 5/7] 请求大模型批量总结 definition（一次请求）...")
        prompt = f"""
你是知识图谱本体工程专家。请基于以下从图谱中确定性提取的类型与样例，为每个类型生成简洁、可用于信息抽取的 definition。

输入数据：
{json.dumps(llm_input, ensure_ascii=False, indent=2)}

输出要求（严格 JSON）：
{{
  "entity_definitions": [
    {{"name": "实体类型名", "definition": "定义"}}
  ],
  "relation_definitions": [
    {{
      "name": "关系类型名",
      "definition": "定义",
      "source_entity_type": "源类型（可空，若不确定保持输入值）",
      "target_entity_type": "目标类型（可空，若不确定保持输入值）"
    }}
  ]
}}
"""
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是严谨的本体定义生成器，只返回 JSON。"},
                {"role": "user", "content": prompt},
            ],
            complexity=COMPLEXITY_NORMAL,
            stream_log=True,
            max_tokens=3000,
        )

        for item in result.get("entity_definitions", []) or []:
            name = str(item.get("name", "")).strip()
            definition = str(item.get("definition", "")).strip()
            if name:
                llm_defs_by_entity[name] = definition

        for item in result.get("relation_definitions", []) or []:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            llm_defs_by_relation[name] = {
                "definition": str(item.get("definition", "")).strip(),
                "source_entity_type": str(item.get("source_entity_type", "")).strip(),
                "target_entity_type": str(item.get("target_entity_type", "")).strip(),
            }
        log_extraction("[Step 5/7] definition 批量总结完成")
    except Exception as e:
        log_extraction(f"[Step 5/7] LLM 定义总结失败，回退到规则定义：{e}", "WARNING")

    # Step 6: 组装实体类型
    log_extraction("[Step 6/7] 生成实体类型与关系类型...")
    entity_types: List[EntityType] = []
    for i, et in enumerate(entity_types_found):
        examples = entity_samples.get(et, [])[:3]
        entity_types.append(
            EntityType(
                name=et,
                definition=llm_defs_by_entity.get(et) or f"从导入图谱中识别出的「{et}」类型实体。",
                examples=examples,
                color=COLORS[i % len(COLORS)],
            )
        )

    # 组装关系类型并融合 LLM 定义
    relation_types: List[RelationType] = []
    for rt in relation_types_found:
        src_type, tgt_type = relation_major_pairs.get(rt, ("", ""))
        llm_rel = llm_defs_by_relation.get(rt, {})
        src_type_final = llm_rel.get("source_entity_type") or src_type
        tgt_type_final = llm_rel.get("target_entity_type") or tgt_type
        relation_types.append(
            RelationType(
                name=rt,
                definition=llm_rel.get("definition") or f"从导入图谱中识别出的「{rt}」关系。",
                source_entity_type=src_type_final,
                target_entity_type=tgt_type_final,
                examples=relation_examples.get(rt, [])[:3],
            )
        )

    # Step 7: 返回结果
    schema = SchemaConfig(entity_types=entity_types, relation_types=relation_types)
    log_extraction(
        f"[Step 7/7] Schema 生成完成：entity_types={len(entity_types)}, relation_types={len(relation_types)}"
    )
    return schema
