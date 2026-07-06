"""
图谱数据路由
"""
import json
import uuid
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from models.graph import GraphData, Node, Edge
from config import get_project_dir
from services.graph_store import (
    load_draft_graph,
    save_draft_graph,
    publish_graph,
    reject_graph,
    load_published_graph,
    get_subgraph,
    get_nx_graph,
    _build_draft_nx_graph,
    sync_entity_index,
    get_publish_validation_report,
)

router = APIRouter()


# ===== 请求模型 =====
class NodeUpdateRequest(BaseModel):
    """节点更新请求"""
    name: Optional[str] = None
    entity_type: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None


class EdgeUpdateRequest(BaseModel):
    """边更新请求"""
    relation_type: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None


@router.get("/{project_id}/graph")
async def get_graph(project_id: str, status: str = "draft"):
    """获取图谱数据"""
    if status == "published":
        graph = load_published_graph(project_id)
    else:
        graph = load_draft_graph(project_id)
    return graph.model_dump()


@router.get("/{project_id}/graph/validate")
async def validate_project_graph(project_id: str):
    """发布前确定性校验预览（CES 执行层）：返回可发布项与违规清单，不修改数据。"""
    return get_publish_validation_report(project_id)


@router.post("/{project_id}/graph/publish")
async def publish_project_graph(project_id: str):
    """发布图谱（审核通过）。启用门控时仅发布通过确定性校验的节点/边。"""
    try:
        graph = publish_graph(project_id)
        return {
            "message": "图谱已发布",
            "version": graph.version,
            "published_nodes": len(graph.nodes),
            "published_edges": len(graph.edges),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/graph/reject")
async def reject_project_graph(project_id: str):
    """拒绝图谱（重置草稿）"""
    reject_graph(project_id)
    return {"message": "图谱已拒绝，可重新配置后抽取"}


@router.get("/{project_id}/graph/subgraph")
async def get_project_subgraph(project_id: str, node_ids: str, depth: int = 1, status: str = "published", direction: str = "both"):
    """获取子图数据（逗号分隔的 node_ids），支持方向：up, down, both"""
    id_list = [i.strip() for i in node_ids.split(",") if i.strip()]
    try:
        subgraph = get_subgraph(project_id, id_list, depth=depth, status=status, direction=direction)
        return subgraph.model_dump()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/graph/search")
async def search_entities(project_id: str, query: str, status: str = "published"):
    """在图谱中搜索实体（用于自动补全或起始节点选择）"""
    G = get_nx_graph(project_id) if status == "published" else _build_draft_nx_graph(project_id)
    query_lower = query.lower()
    results = []
    
    for node_id, data in G.nodes(data=True):
        name = data.get("name", "")
        if query_lower in name.lower():
            results.append({
                "id": node_id,
                "name": name,
                "type": data.get("entity_type", "Unknown")
            })
            if len(results) >= 20: # 限制返回数量
                break
                
    return results


@router.get("/{project_id}/graph/export")
async def export_graph(project_id: str):
    """导出图谱为 JSON"""
    graph = load_published_graph(project_id)
    if not graph.nodes:
        graph = load_draft_graph(project_id)
    return JSONResponse(
        content=graph.model_dump(),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=graph_{project_id}.json"},
    )


# ===== 节点 CRUD =====

@router.patch("/{project_id}/graph/nodes/{node_id}")
async def update_node(project_id: str, node_id: str, req: NodeUpdateRequest):
    """
    更新节点属性。
    如果 name 发生变化，自动级联更新引用该节点的所有边的显示名称。
    """
    graph = load_draft_graph(project_id)

    target_node = None
    for node in graph.nodes:
        if node.id == node_id:
            target_node = node
            break

    if not target_node:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 记录旧名称（用于级联）
    old_name = target_node.name
    before_snapshot = {"name": target_node.name, "entity_type": target_node.entity_type}

    # 应用更新
    if req.name is not None:
        target_node.name = req.name
    if req.entity_type is not None:
        target_node.entity_type = req.entity_type
    if req.properties is not None:
        target_node.properties = req.properties
    if req.confidence is not None:
        target_node.confidence = req.confidence

    save_draft_graph(project_id, graph)

    # 记录反思案例（类型或名称变化才有学习价值）
    try:
        if before_snapshot["entity_type"] != target_node.entity_type or before_snapshot["name"] != target_node.name:
            from services.reflection import record_case
            record_case(project_id, "entity", "modify", before_snapshot,
                        {"name": target_node.name, "entity_type": target_node.entity_type})
    except Exception:
        pass

    return {"message": "节点已更新", "node": target_node.model_dump()}


@router.delete("/{project_id}/graph/nodes/{node_id}")
async def delete_node(project_id: str, node_id: str):
    """
    删除节点，并自动清理所有引用该节点的边。
    """
    graph = load_draft_graph(project_id)

    # 检查节点是否存在
    deleted_node = next((n for n in graph.nodes if n.id == node_id), None)
    original_count = len(graph.nodes)
    graph.nodes = [n for n in graph.nodes if n.id != node_id]
    if len(graph.nodes) == original_count:
        raise HTTPException(status_code=404, detail="节点不存在")

    # 记录反思案例：人工删除的实体
    try:
        if deleted_node:
            from services.reflection import record_case
            record_case(project_id, "entity", "delete",
                        {"name": deleted_node.name, "entity_type": deleted_node.entity_type})
    except Exception:
        pass

    # 清理引用该节点的边
    removed_edges = [e.id for e in graph.edges
                     if e.source_id == node_id or e.target_id == node_id]
    graph.edges = [e for e in graph.edges
                   if e.source_id != node_id and e.target_id != node_id]

    save_draft_graph(project_id, graph)
    return {
        "message": "节点已删除",
        "removed_edges": removed_edges,
        "remaining_nodes": len(graph.nodes),
        "remaining_edges": len(graph.edges),
    }


# ===== 边 CRUD =====

@router.patch("/{project_id}/graph/edges/{edge_id}")
async def update_edge(project_id: str, edge_id: str, req: EdgeUpdateRequest):
    """更新边属性"""
    graph = load_draft_graph(project_id)

    target_edge = None
    for edge in graph.edges:
        if edge.id == edge_id:
            target_edge = edge
            break

    if not target_edge:
        raise HTTPException(status_code=404, detail="关系不存在")

    before_rel = {"relation_type": target_edge.relation_type}

    if req.relation_type is not None:
        target_edge.relation_type = req.relation_type
    if req.properties is not None:
        target_edge.properties = req.properties
    if req.confidence is not None:
        target_edge.confidence = req.confidence

    save_draft_graph(project_id, graph)

    try:
        if req.relation_type is not None and before_rel["relation_type"] != target_edge.relation_type:
            from services.reflection import record_case
            record_case(project_id, "relation", "modify", before_rel,
                        {"relation_type": target_edge.relation_type})
    except Exception:
        pass

    return {"message": "关系已更新", "edge": target_edge.model_dump()}


@router.delete("/{project_id}/graph/edges/{edge_id}")
async def delete_edge(project_id: str, edge_id: str):
    """删除边"""
    graph = load_draft_graph(project_id)

    deleted_edge = next((e for e in graph.edges if e.id == edge_id), None)
    original_count = len(graph.edges)
    graph.edges = [e for e in graph.edges if e.id != edge_id]
    if len(graph.edges) == original_count:
        raise HTTPException(status_code=404, detail="关系不存在")

    save_draft_graph(project_id, graph)

    try:
        if deleted_edge:
            from services.reflection import record_case
            record_case(project_id, "relation", "delete",
                        {"relation_type": deleted_edge.relation_type})
    except Exception:
        pass

    return {"message": "关系已删除", "remaining_edges": len(graph.edges)}


# ===== 图谱数据导入（冷启动） =====

# JSON 模版内容
GRAPH_TEMPLATE = {
    "nodes": [
        {
            "name": "示例实体名称",
            "entity_type": "实体类型",
            "properties": {"key": "value"},
            "confidence": 1.0,
        }
    ],
    "edges": [
        {
            "source_name": "源实体名称",
            "target_name": "目标实体名称",
            "relation_type": "关系类型",
            "properties": {},
            "confidence": 1.0,
        }
    ],
}


@router.get("/{project_id}/graph/template")
async def download_graph_template(project_id: str):
    """下载图谱数据 JSON 模版"""
    return JSONResponse(
        content=GRAPH_TEMPLATE,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=graph_template_{project_id}.json"
        },
    )


@router.post("/{project_id}/graph/import")
async def import_graph_data(project_id: str, file: UploadFile = File(...)):
    """
    导入图谱数据（冷启动）。
    接受按模版格式整理的 JSON 文件，校验后合并到项目草稿图谱。
    边使用 source_name/target_name 引用节点，系统自动解析为 ID。
    """
    # 1. 读取并解析 JSON
    filename = file.filename or "unknown.json"
    if not filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="仅支持 JSON 文件")

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {str(e)}")

    # 2. 校验顶层结构
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON 顶层必须是对象")

    raw_nodes: List[Dict] = data.get("nodes", [])
    raw_edges: List[Dict] = data.get("edges", [])

    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise HTTPException(status_code=400, detail="nodes 和 edges 必须是数组")

    if not raw_nodes and not raw_edges:
        raise HTTPException(status_code=400, detail="至少需要提供节点或边数据")

    # 3. 加载已有图谱
    graph = load_draft_graph(project_id)
    existing_name_map: Dict[str, Node] = {n.name: n for n in graph.nodes}

    # 4. 处理节点
    import_stats = {"new_nodes": 0, "merged_nodes": 0, "new_edges": 0, "skipped_edges": 0}
    name_to_id: Dict[str, str] = {n.name: n.id for n in graph.nodes}

    for raw_node in raw_nodes:
        name = raw_node.get("name", "").strip()
        entity_type = raw_node.get("entity_type", "").strip()
        if not name or not entity_type:
            continue

        if name in existing_name_map:
            # 同名实体合并：更新属性
            existing = existing_name_map[name]
            if raw_node.get("properties"):
                existing.properties.update(raw_node["properties"])
            if "cold_start" not in existing.source_chunk_ids:
                existing.source_chunk_ids.append("cold_start")
            import_stats["merged_nodes"] += 1
        else:
            # 创建新节点
            node = Node(
                name=name,
                entity_type=entity_type,
                properties=raw_node.get("properties", {}),
                source_chunk_ids=["cold_start"],
                confidence=raw_node.get("confidence", 1.0),
            )
            graph.nodes.append(node)
            existing_name_map[name] = node
            name_to_id[name] = node.id
            import_stats["new_nodes"] += 1

    # 确保 name_to_id 包含所有节点
    for n in graph.nodes:
        name_to_id[n.name] = n.id

    # 5. 处理边
    existing_edge_keys = {
        (e.source_id, e.target_id, e.relation_type) for e in graph.edges
    }

    for raw_edge in raw_edges:
        source_name = raw_edge.get("source_name", "").strip()
        target_name = raw_edge.get("target_name", "").strip()
        relation_type = raw_edge.get("relation_type", "").strip()

        if not source_name or not target_name or not relation_type:
            import_stats["skipped_edges"] += 1
            continue

        source_id = name_to_id.get(source_name)
        target_id = name_to_id.get(target_name)

        if not source_id or not target_id:
            import_stats["skipped_edges"] += 1
            continue

        # 去重检查
        edge_key = (source_id, target_id, relation_type)
        if edge_key in existing_edge_keys:
            import_stats["skipped_edges"] += 1
            continue

        edge = Edge(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            properties=raw_edge.get("properties", {}),
            source_chunk_ids=["cold_start"],
            confidence=raw_edge.get("confidence", 1.0),
        )
        graph.edges.append(edge)
        existing_edge_keys.add(edge_key)
        import_stats["new_edges"] += 1

    # 6. 保存
    save_draft_graph(project_id, graph)

    # 7. 更新实体索引
    try:
        sync_entity_index(project_id, status="draft")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"导入后同步实体索引失败: {e}")

    return {
        "message": "图谱数据导入成功",
        "stats": import_stats,
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
    }

