import json
import sqlite3
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
import networkx as nx
import aiosqlite
from models.graph import GraphData, Node, Edge
from config import get_project_dir
import numpy as np

logger = logging.getLogger(__name__)


_nx_cache: Dict[str, nx.DiGraph] = {}

# 系统保留的实体/关系类型（不要求出现在用户 Schema 中）
# - 未归类片段：未抽取出任何本体实体的文本片段的保底挂载节点
# - 文档片段：每个片段的“顺序锚点”，用于连接 下一段 关系
RESERVED_ENTITY_TYPES = {"未归类片段", "文档片段", "未分类实体", "未知类型"}
RESERVED_RELATION_TYPES = {"下一段"}

def get_nx_graph(project_id: str) -> nx.DiGraph:
    """获取 networkx 格式的已发布图结构（带缓存）"""
    if project_id in _nx_cache:
        return _nx_cache[project_id]
        
    graph_data = load_published_graph(project_id)
    
    G = nx.DiGraph()
    for node in graph_data.nodes:
        G.add_node(
            node.id, 
            name=node.name, 
            entity_type=node.entity_type, 
            source_chunk_ids=node.source_chunk_ids,
            evidence_quotes=node.evidence_quotes,
            properties=node.properties
        )
        
    for edge in graph_data.edges:
        G.add_edge(
            edge.source_id, 
            edge.target_id, 
            id=edge.id,
            relation_type=edge.relation_type,
            source_chunk_ids=edge.source_chunk_ids,
            evidence_quotes=edge.evidence_quotes,
            properties=edge.properties
        )
        
    _nx_cache[project_id] = G
    return G

def clear_nx_cache(project_id: str):
    """清除网络图缓存"""
    if project_id in _nx_cache:
        del _nx_cache[project_id]

def _db_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "graph.db"

def _snapshots_dir(project_id: str) -> Path:
    d = get_project_dir(project_id) / "snapshots"
    d.mkdir(exist_ok=True)
    return d

def init_db_sync(project_id: str):
    """同步初始化数据库表"""
    db_file = _db_path(project_id)
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    
    # 节点表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT,
            source_chunk_ids TEXT,
            evidence_quotes TEXT,
            confidence REAL,
            status TEXT DEFAULT 'draft',
            PRIMARY KEY (id, status)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name)')
    
    # 边表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            id TEXT,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            properties TEXT,
            source_chunk_ids TEXT,
            evidence_quotes TEXT,
            confidence REAL,
            status TEXT DEFAULT 'draft',
            PRIMARY KEY (id, status)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_edges_status ON edges(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source_id, target_id)')
    # 获取最后版本号的设置表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    # 额外索引优化：确保实体名和类型有索引，加速精确/模糊匹配
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(entity_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(relation_type)')

    # 兼容旧库：补齐 evidence_quotes 列
    _ensure_column(cursor, "nodes", "evidence_quotes", "TEXT")
    _ensure_column(cursor, "edges", "evidence_quotes", "TEXT")

    conn.commit()
    conn.close()


def _ensure_column(cursor, table: str, column: str, col_type: str):
    """SQLite 不支持 ADD COLUMN IF NOT EXISTS，手动检测后补列。"""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

async def init_db(project_id: str):
    """异步初始化数据库表"""
    init_db_sync(project_id) # Reuse synchronous logic for initial creation

def _parse_node(row) -> Node:
    return Node(
        id=row[0],
        name=row[1],
        entity_type=row[2],
        properties=json.loads(row[3]) if row[3] else {},
        source_chunk_ids=json.loads(row[4]) if row[4] else [],
        evidence_quotes=json.loads(row[5]) if len(row) > 5 and row[5] else [],
        confidence=row[6] if len(row) > 6 else (row[5] if len(row) > 5 else 1.0),
    )

def _parse_edge(row) -> Edge:
    return Edge(
        id=row[0],
        source_id=row[1],
        target_id=row[2],
        relation_type=row[3],
        properties=json.loads(row[4]) if row[4] else {},
        source_chunk_ids=json.loads(row[5]) if row[5] else [],
        evidence_quotes=json.loads(row[6]) if len(row) > 6 and row[6] else [],
        confidence=row[7] if len(row) > 7 else (row[6] if len(row) > 6 else 1.0),
    )

def _load_graph_sync(project_id: str, status: str) -> GraphData:
    """同步加载指定状态的图谱"""
    init_db_sync(project_id)
    conn = sqlite3.connect(str(_db_path(project_id)))
    cursor = conn.cursor()
    
    # Load nodes
    cursor.execute("SELECT id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence FROM nodes WHERE status = ?", (status,))
    nodes = [_parse_node(row) for row in cursor.fetchall()]
    
    # Load edges
    cursor.execute("SELECT id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence FROM edges WHERE status = ?", (status,))
    edges = [_parse_edge(row) for row in cursor.fetchall()]
    
    # Load version
    cursor.execute("SELECT value FROM meta WHERE key = 'version'")
    row = cursor.fetchone()
    version = int(row[0]) if row else 0
    
    cursor.execute("SELECT value FROM meta WHERE key = 'updated_at'")
    row = cursor.fetchone()
    updated_at = row[0] if row else datetime.now().isoformat()
    
    conn.close()
    
    return GraphData(
        nodes=nodes,
        edges=edges,
        version=version,
        status=status,
        updated_at=updated_at
    )

def load_draft_graph(project_id: str) -> GraphData:
    return _load_graph_sync(project_id, "draft")

def load_published_graph(project_id: str) -> GraphData:
    return _load_graph_sync(project_id, "published")

def save_draft_graph(project_id: str, graph: GraphData):
    """同步全量覆盖保存草稿图谱 (用于前端小批量更新, 取代旧的 JSON 全量更新)"""
    init_db_sync(project_id)
    conn = sqlite3.connect(str(_db_path(project_id)))
    cursor = conn.cursor()
    
    try:
        # 删除现有的草稿状态数据
        cursor.execute("DELETE FROM edges WHERE status = 'draft'")
        cursor.execute("DELETE FROM nodes WHERE status = 'draft'")
        
        # 插入新的草稿节点
        for node in graph.nodes:
            cursor.execute('''
                INSERT INTO nodes (id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
            ''', (node.id, node.name, node.entity_type, json.dumps(node.properties), json.dumps(node.source_chunk_ids), json.dumps(getattr(node, "evidence_quotes", [])), node.confidence))
            
        # 插入新的草稿边
        for edge in graph.edges:
            cursor.execute('''
                INSERT INTO edges (id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            ''', (edge.id, edge.source_id, edge.target_id, edge.relation_type, json.dumps(edge.properties), json.dumps(edge.source_chunk_ids), json.dumps(getattr(edge, "evidence_quotes", [])), edge.confidence))
            
        # 更新 meta 信息
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('updated_at', ?)", (datetime.now().isoformat(),))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_publish_validation_report(project_id: str) -> Dict:
    """对当前草稿运行确定性发布门控并返回结构化报告（不修改数据，供预览）。"""
    from services.validation import validate_for_publish
    from config import load_config

    graph = load_draft_graph(project_id)
    schema = _load_schema_dict(project_id)
    config = load_config()
    require_evidence = bool(getattr(config, "publish_gate_require_evidence", False))
    report = validate_for_publish(graph, schema, require_evidence=require_evidence)
    return report.to_dict()


def _load_schema_dict(project_id: str) -> Optional[Dict]:
    schema_path = get_project_dir(project_id) / "schema.json"
    if not schema_path.exists():
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def publish_graph(project_id: str) -> GraphData:
    """发布草稿节点为正式节点。

    启用 enable_publish_gate 时，先经过确定性校验门控（CES 执行层）：
    - block 模式：存在违规则发布失败，返回违规清单；
    - filter 模式（默认）：仅将通过校验的节点/边写入 published。
    """
    from config import load_config

    graph = load_draft_graph(project_id)

    if not graph.nodes:
        raise ValueError("图谱为空，无法发布")

    config = load_config()
    gate_enabled = bool(getattr(config, "enable_publish_gate", True))

    allowed_node_ids: Optional[set] = None
    allowed_edge_ids: Optional[set] = None

    if gate_enabled:
        from services.validation import validate_for_publish

        schema = _load_schema_dict(project_id)
        require_evidence = bool(getattr(config, "publish_gate_require_evidence", False))
        report = validate_for_publish(graph, schema, require_evidence=require_evidence)

        if not report.passed:
            logger.warning(
                f"[发布门控] 检测到 {len(report.violations)} 条违规 "
                f"(拒绝节点 {report.stats.get('rejected_nodes', 0)}, 拒绝边 {report.stats.get('rejected_edges', 0)})"
            )
            if bool(getattr(config, "publish_gate_block", False)):
                sample = "; ".join(v.message for v in report.violations[:5])
                raise ValueError(
                    f"发布门控未通过：{len(report.violations)} 条违规。示例：{sample}"
                )
        allowed_node_ids = report.valid_node_ids
        allowed_edge_ids = report.valid_edge_ids
    else:
        # 兼容旧路径：仅记录日志
        errors = validate_graph(graph, project_id)
        if errors:
            logger.warning(f"图谱校验存在警告 (发布时将自动过滤无效连接): {'; '.join(errors)}")

    init_db_sync(project_id)
    conn = sqlite3.connect(str(_db_path(project_id)))
    cursor = conn.cursor()
    
    try:
        # 获取新版本号
        cursor.execute("SELECT value FROM meta WHERE key = 'version'")
        row = cursor.fetchone()
        new_version = (int(row[0]) if row else 0) + 1
        
        # 删除旧的发布数据
        cursor.execute("DELETE FROM edges WHERE status = 'published'")
        cursor.execute("DELETE FROM nodes WHERE status = 'published'")

        if allowed_node_ids is None:
            # 门控关闭：全量复制（兼容旧路径）
            cursor.execute('''
                INSERT INTO nodes (id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                SELECT id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence, 'published'
                FROM nodes WHERE status = 'draft'
            ''')
            cursor.execute('''
                INSERT INTO edges (id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                SELECT id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence, 'published'
                FROM edges WHERE status = 'draft'
                AND source_id IN (SELECT id FROM nodes WHERE status = 'draft')
                AND target_id IN (SELECT id FROM nodes WHERE status = 'draft')
            ''')
        else:
            # 门控开启：仅写入通过校验的节点/边
            for node in graph.nodes:
                if node.id not in allowed_node_ids:
                    continue
                cursor.execute('''
                    INSERT INTO nodes (id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'published')
                ''', (node.id, node.name, node.entity_type, json.dumps(node.properties),
                      json.dumps(node.source_chunk_ids), json.dumps(node.evidence_quotes), node.confidence))
            for edge in graph.edges:
                if edge.id not in allowed_edge_ids:
                    continue
                cursor.execute('''
                    INSERT INTO edges (id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published')
                ''', (edge.id, edge.source_id, edge.target_id, edge.relation_type, json.dumps(edge.properties),
                      json.dumps(edge.source_chunk_ids), json.dumps(edge.evidence_quotes), edge.confidence))

        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)", (str(new_version),))
        updated_at = datetime.now().isoformat()
        cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('updated_at', ?)", (updated_at,))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    # 重新加载发布态用于快照与返回（确保与门控过滤后的实际数据一致）
    published = _load_graph_sync(project_id, "published")
    published.version = new_version
    published.status = "published"
    published.updated_at = updated_at

    # 保存快照 (保留旧行为以备后用)
    snapshot_file = _snapshots_dir(project_id) / f"v{new_version}.json"
    with open(snapshot_file, "w", encoding="utf-8") as f:
        json.dump(published.model_dump(), f, ensure_ascii=False, indent=2)

    clear_nx_cache(project_id)

    # 同步实体索引到向量库
    try:
        sync_entity_index(project_id, status="published")
    except Exception as e:
        logger.error(f"发布时同步实体索引失败: {e}")

    # 可选：发布时构建社区摘要（Microsoft GraphRAG 模式）
    try:
        if bool(getattr(config, "enable_community_on_publish", False)):
            from services.community import build_communities
            build_communities(project_id)
    except Exception as e:
        logger.error(f"发布时构建社区摘要失败: {e}")

    return published

def reject_graph(project_id: str):
    init_db_sync(project_id)
    conn = sqlite3.connect(str(_db_path(project_id)))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM edges WHERE status = 'draft'")
    cursor.execute("DELETE FROM nodes WHERE status = 'draft'")
    conn.commit()
    conn.close()

def validate_graph(graph: GraphData, project_id: str) -> List[str]:
    errors = []
    node_ids = {n.id for n in graph.nodes}

    for edge in graph.edges:
        if edge.source_id not in node_ids:
            errors.append(f"边 {edge.id} 引用了不存在的源节点 {edge.source_id}")
        if edge.target_id not in node_ids:
            errors.append(f"边 {edge.id} 引用了不存在的目标节点 {edge.target_id}")

    schema_path = get_project_dir(project_id) / "schema.json"
    if schema_path.exists():
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        entity_type_names = {et["name"] for et in schema.get("entity_types", [])}
        relation_type_names = {rt["name"] for rt in schema.get("relation_types", [])}

        for node in graph.nodes:
            if entity_type_names and node.entity_type not in entity_type_names and node.entity_type not in RESERVED_ENTITY_TYPES:
                errors.append(f"节点 {node.name} 的类型 {node.entity_type} 不在 Schema 中")

        for edge in graph.edges:
            if relation_type_names and edge.relation_type not in relation_type_names and edge.relation_type not in RESERVED_RELATION_TYPES:
                errors.append(f"边 {edge.id} 的关系类型 {edge.relation_type} 不在 Schema 中")

    return errors

# ==========================================
# 异步操作 API，供后台并发抽取使用
# ==========================================

async def add_nodes_to_draft(project_id: str, nodes: List[Node]):
    """向草稿图谱中添加节点 (异步单条插入并忽略重复主键)"""
    if not nodes:
        return
        
    await init_db(project_id)
    async with aiosqlite.connect(str(_db_path(project_id))) as db:
        for node in nodes:
            node_evidence = getattr(node, "evidence_quotes", []) or []
            # Check if exists by name in draft
            async with db.execute("SELECT id, source_chunk_ids, evidence_quotes FROM nodes WHERE name = ? AND status = 'draft'", (node.name,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    # Update source_chunk_ids + 合并证据
                    existing_id = row[0]
                    existing_chunks = json.loads(row[1]) if row[1] else []
                    new_chunks = list(set(existing_chunks + node.source_chunk_ids))
                    existing_ev = json.loads(row[2]) if row[2] else []
                    merged_ev = _merge_evidence(existing_ev, node_evidence)
                    await db.execute("UPDATE nodes SET source_chunk_ids = ?, evidence_quotes = ? WHERE id = ?", (json.dumps(new_chunks), json.dumps(merged_ev), existing_id))
                else:
                    await db.execute('''
                        INSERT OR IGNORE INTO nodes (id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
                    ''', (node.id, node.name, node.entity_type, json.dumps(node.properties), json.dumps(node.source_chunk_ids), json.dumps(node_evidence), node.confidence))
        await db.commit()

async def add_edges_to_draft(project_id: str, edges: List[Edge]):
    """向草稿图谱中添加边 (异步单条插入并忽略重复主键)"""
    if not edges:
        return
        
    await init_db(project_id)
    async with aiosqlite.connect(str(_db_path(project_id))) as db:
        for edge in edges:
            await db.execute('''
                INSERT OR IGNORE INTO edges (id, source_id, target_id, relation_type, properties, source_chunk_ids, evidence_quotes, confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            ''', (edge.id, edge.source_id, edge.target_id, edge.relation_type, json.dumps(edge.properties), json.dumps(edge.source_chunk_ids), json.dumps(getattr(edge, "evidence_quotes", []) or []), edge.confidence))
        await db.commit()

async def get_draft_entity_map(project_id: str) -> Dict[str, Node]:
    """获取草稿图谱的所有节点映射字典，按名字索引"""
    await init_db(project_id)
    entity_map = {}
    async with aiosqlite.connect(str(_db_path(project_id))) as db:
        async with db.execute("SELECT id, name, entity_type, properties, source_chunk_ids, evidence_quotes, confidence FROM nodes WHERE status = 'draft'") as cursor:
            async for row in cursor:
                node = _parse_node(row)
                entity_map[node.name] = node
    return entity_map


def _merge_evidence(existing: List[Dict], incoming: List[Dict], limit: int = 8) -> List[Dict]:
    """合并证据短句并去重（按 chunk_id+quote），限制总量避免膨胀。"""
    merged = list(existing or [])
    seen = {(e.get("chunk_id", ""), e.get("quote", "")) for e in merged}
    for ev in (incoming or []):
        key = (ev.get("chunk_id", ""), ev.get("quote", ""))
        if key not in seen and ev.get("quote"):
            merged.append(ev)
            seen.add(key)
    return merged[:limit]

def get_subgraph(project_id: str, start_node_ids: List[str], depth: int = 1, status: str = "published", direction: str = "both") -> GraphData:
    """
    基于 NetworkX 内存图快速获取子图数据。支持方向：up (前驱), down (后继), both (双向)。
    """
    G = get_nx_graph(project_id) if status == "published" else _build_draft_nx_graph(project_id)
    
    if not start_node_ids:
        return GraphData(nodes=[], edges=[], version=0, status=status)
        
    # 计算子图节点
    nodes_to_include = set()
    current_layer = set(start_node_ids)
    nodes_to_include.update(current_layer)
    
    for _ in range(depth):
        next_layer = set()
        for node_id in current_layer:
            if node_id in G:
                neighbors = []
                if direction == "up":
                    neighbors = list(G.predecessors(node_id))
                elif direction == "down":
                    neighbors = list(G.successors(node_id))
                else: # both
                    neighbors = list(G.successors(node_id)) + list(G.predecessors(node_id))
                next_layer.update(neighbors)
        current_layer = next_layer - nodes_to_include
        nodes_to_include.update(current_layer)
        if not current_layer:
            break
            
    # 构建 GraphData
    sub_nodes = []
    for n_id in nodes_to_include:
        if n_id in G.nodes:
            d = G.nodes[n_id]
            sub_nodes.append(Node(
                id=n_id,
                name=d.get("name", ""),
                entity_type=d.get("entity_type", "Unknown"),
                properties=d.get("properties", {}),
                source_chunk_ids=d.get("source_chunk_ids", []),
                evidence_quotes=d.get("evidence_quotes", []),
                confidence=d.get("confidence", 1.0)
            ))
            
    # 提取节点间的边
    # 注意：如果方向是 up/down，我们可能只想包含该方向的边
    sub_edges = []
    sub_G = G.subgraph(nodes_to_include)
    for u, v, data in sub_G.edges(data=True):
        sub_edges.append(Edge(
            id=data.get("id", str(uuid.uuid4())),
            source_id=u,
            target_id=v,
            relation_type=data.get("relation_type", ""),
            properties=data.get("properties", {}),
            source_chunk_ids=data.get("source_chunk_ids", []),
            evidence_quotes=data.get("evidence_quotes", []),
            confidence=data.get("confidence", 1.0)
        ))
        
    return GraphData(
        nodes=sub_nodes,
        edges=sub_edges,
        version=0,
        status=status,
        updated_at=datetime.now().isoformat()
    )

def _build_draft_nx_graph(project_id: str) -> nx.DiGraph:
    """构建草稿状态的内存图（不带缓存，因为草稿变化频繁）"""
    graph_data = load_draft_graph(project_id)
    G = nx.MultiDiGraph() # 使用 MultiDiGraph 以防有重边
    for node in graph_data.nodes:
        G.add_node(node.id, **node.model_dump())
    for edge in graph_data.edges:
        G.add_edge(edge.source_id, edge.target_id, **edge.model_dump())
    return G

def get_entity_type_samples(project_id: str, entity_type: str, limit: int = 3, status: str = "published") -> List[Node]:
    """获取指定实体的样例"""
    G = get_nx_graph(project_id) if status == "published" else _build_draft_nx_graph(project_id)
    samples = []
    for node_id, data in G.nodes(data=True):
        if data.get("entity_type") == entity_type:
            samples.append(Node(
                id=node_id,
                name=data.get("name", ""),
                entity_type=data.get("entity_type", ""),
                properties=data.get("properties", {}),
                source_chunk_ids=data.get("source_chunk_ids", []),
                confidence=data.get("confidence", 1.0)
            ))
            if len(samples) >= limit:
                break
    return samples

def get_relation_type_samples(project_id: str, relation_type: str, limit: int = 3, status: str = "published") -> List[Edge]:
    """获取指定关系的样例"""
    G = get_nx_graph(project_id) if status == "published" else _build_draft_nx_graph(project_id)
    samples = []
    for u, v, data in G.edges(data=True):
        if data.get("relation_type") == relation_type:
            samples.append(Edge(
                id=data.get("id", str(uuid.uuid4())),
                source_id=u,
                target_id=v,
                relation_type=data.get("relation_type", ""),
                properties=data.get("properties", {}),
                source_chunk_ids=data.get("source_chunk_ids", []),
                confidence=data.get("confidence", 1.0)
            ))
            if len(samples) >= limit:
                break
    return samples

def sync_entity_index(project_id: str, status: str = "published"):
    """
    同步指定状态的实体到向量索引 (entity.index)。
    通常在发布图谱或大批量导入数据后调用。
    """
    from config import load_config

    config = load_config()
    if str(getattr(config, "similarity_backend", "keyword")).lower() != "vector":
        logger.info("[VectorStore] 当前为快速相似度模式，跳过实体索引同步")
        return

    from services.vector_store import VectorStore
    from services.embedding import embed_texts
    
    logger.info(f"[VectorStore] 正在同步项目 {project_id} 的实体索引 ({status})...")
    
    # 获取图谱数据
    graph = load_published_graph(project_id) if status == "published" else load_draft_graph(project_id)
    if not graph.nodes:
        logger.info("[VectorStore] 图谱节点为空，跳过索引同步")
        return

    # 初始化向量存储（使用 entity 分支）
    v_store = VectorStore(get_project_dir(project_id), name="entity")
    v_store.clear() # 全量重建以保证一致性 (对于实体这种中等规模数据，重建比增量更稳健)
    
    # 分批处理以防 OOM 或超时
    batch_size = 100
    for i in range(0, len(graph.nodes), batch_size):
        batch = graph.nodes[i : i + batch_size]
        texts = [n.name for n in batch]
        
        try:
            vectors = embed_texts(texts)
            metas = [
                {"node_id": n.id, "name": n.name, "entity_type": n.entity_type}
                for n in batch
            ]
            v_store.add(vectors, metas)
        except Exception as e:
            logger.error(f"[VectorStore] 同步实体批次失败: {e}")
            
    logger.info(f"[VectorStore] 实体索引同步完成: {v_store.total_count} 条记录")
