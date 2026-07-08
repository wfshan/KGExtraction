"""
Schema 配置路由
"""
import json
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from models.schema import SchemaConfig, SchemaSuggestionRequest, SchemaChatRequest
from config import get_project_dir
from services.extraction_logger import set_run_context
import os

router = APIRouter()


def _get_schema_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "schema.json"


def _has_documents(project_id: str) -> bool:
    """检查项目是否有已解析的文档（chunks 数据）"""
    project_dir = get_project_dir(project_id)
    chunks_dir = project_dir / "chunks"
    if not chunks_dir.exists():
        return False
    return any(chunks_dir.glob("*_chunks.json"))


def _has_graph_data(project_id: str) -> bool:
    """检查项目是否有冷启动导入的图谱 JSON 数据"""
    from services.graph_store import load_draft_graph
    graph = load_draft_graph(project_id)
    for node in graph.nodes:
        if "cold_start" in node.source_chunk_ids:
            return True
    return False


def _resolve_source(project_id: str, source: str) -> str:
    """将 source='auto' 解析为具体来源"""
    if source in ("documents", "graph"):
        return source
    # auto: 优先文档，次之图谱
    if _has_documents(project_id):
        return "documents"
    if _has_graph_data(project_id):
        return "graph"
    return "documents"  # fallback


def _load_schema(project_id: str) -> SchemaConfig:
    schema_file = _get_schema_file(project_id)
    if not schema_file.exists():
        return SchemaConfig()
    with open(schema_file, "r", encoding="utf-8") as f:
        return SchemaConfig(**json.load(f))


def _save_schema(project_id: str, schema: SchemaConfig):
    schema_file = _get_schema_file(project_id)
    with open(schema_file, "w", encoding="utf-8") as f:
        json.dump(schema.model_dump(), f, ensure_ascii=False, indent=2)


# ===== 数据源查询 =====

@router.get("/{project_id}/schema/sources")
async def get_schema_sources(project_id: str):
    """获取当前项目可用的 Schema 数据源列表"""
    sources = []
    if _has_documents(project_id):
        sources.append({"key": "documents", "label": "上传文档"})
    if _has_graph_data(project_id):
        sources.append({"key": "graph", "label": "导入的图谱 JSON"})
    return {"sources": sources}


@router.get("/{project_id}/schema", response_model=SchemaConfig)
async def get_schema(project_id: str):
    """获取 Schema 配置"""
    return _load_schema(project_id)


@router.put("/{project_id}/schema", response_model=SchemaConfig)
async def update_schema(project_id: str, schema: SchemaConfig):
    """更新 Schema 配置"""
    _save_schema(project_id, schema)
    return schema


@router.post("/{project_id}/schema/suggest", response_model=SchemaConfig)
async def suggest_schema(project_id: str, req: SchemaSuggestionRequest = None):
    """基于文档内容或图谱数据智能建议 Schema"""
    if req is None:
        req = SchemaSuggestionRequest()

    from services.schema_suggestion import generate_schema_suggestion, extract_schema_from_graph_data

    project_dir = get_project_dir(project_id)
    # 强制清理旧的 schema.log
    log_file = project_dir / "logs" / "schema.log"
    if log_file.exists():
        try:
            os.remove(log_file)
        except Exception:
            pass
            
    # 设置日志流式上下文
    set_run_context(project_id, "schema")
    from services.extraction_logger import log_extraction
    log_extraction("=== 开始执行 Schema 智能建议 ===")

    resolved = _resolve_source(project_id, req.source)
    log_extraction(f"[Step 0] 请求来源: {req.source} -> 实际来源: {resolved}")

    if resolved == "graph":
        log_extraction("[Step 1] 进入图谱 JSON 反推流程")
        schema = await extract_schema_from_graph_data(project_id)
        log_extraction("[Step 2] 图谱 JSON 反推完成，写入 schema.json")
        _save_schema(project_id, schema)
        log_extraction("=== Schema 智能建议完成 ===")
        return schema

    # documents 流程（分层抽样 + Map-Reduce 领域概括在 generate_schema_suggestion 内部完成）
    log_extraction("[Step 1] 进入文档分层抽样与领域概括流程")
    schema = await generate_schema_suggestion(project_id, req.sample_size)
    log_extraction("[Step 3] 文档建议完成，写入 schema.json")
    _save_schema(project_id, schema)
    log_extraction("=== Schema 智能建议完成 ===")
    return schema


@router.get("/{project_id}/schema/profile-summary")
async def get_profile_summary_stream(project_id: str, source: str = Query("auto")):
    """流式返回文档开场白，供对话配置 Drawer 打开时自动播报。"""
    resolved = _resolve_source(project_id, source)
    from services.schema_suggestion import stream_profile_summary
    return StreamingResponse(
        stream_profile_summary(project_id, source=resolved),
        media_type="text/event-stream",
    )


@router.post("/{project_id}/schema/chat")
async def chat_with_schema(project_id: str, req: SchemaChatRequest):
    """Schema 辅助设计流式对话接口"""
    from services.schema_suggestion import chat_schema_stream
    
    resolved = _resolve_source(project_id, req.source)

    return StreamingResponse(
        chat_schema_stream(project_id, req.messages, source=resolved),
        media_type="text/event-stream"
    )

@router.post("/{project_id}/schema/generate-from-chat", response_model=SchemaConfig)
async def generate_schema_from_chat_api(project_id: str, req: SchemaChatRequest):
    """根据多轮对话历史生成正式结构化 Schema"""
    from services.schema_suggestion import generate_schema_from_chat
    
    project_dir = get_project_dir(project_id)
    # 强制清理旧的 schema.log
    log_file = project_dir / "logs" / "schema.log"
    if log_file.exists():
        try:
            os.remove(log_file)
        except Exception:
            pass
            
    # 设置日志流式上下文
    set_run_context(project_id, "schema")
    
    schema = await generate_schema_from_chat(req.messages)
    _save_schema(project_id, schema)
    return schema
