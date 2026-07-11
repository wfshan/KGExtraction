from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
from services.graph_rag import stream_chat_rag
from services.chat_store import load_history, clear_history, DEFAULT_SESSION
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class GraphRAGOptions(BaseModel):
    """问图检索配置：检索方式、检索深度（度）与起点实体数量上限"""
    retrieval_mode: Optional[str] = Field(default="auto", description="检索模式: auto(按查询自动路由), graph_flow, graph_full, graph_path, hippo, global, text_only, direct")
    max_degree: Optional[int] = Field(default=2, ge=1, le=5, description="检索深度，如 1/2/3 度展开")
    max_start_entities: Optional[int] = Field(default=5, ge=1, le=100, description="起点实体数量上限")


class ChatRequest(BaseModel):
    query: str
    options: Optional[GraphRAGOptions] = None
    session_id: Optional[str] = Field(default=DEFAULT_SESSION, description="会话标识：多会话/多用户的对话历史相互隔离")


@router.post("/{project_id}/graph-rag/chat")
async def chat_with_graph(project_id: str, req: ChatRequest):
    """
    问图：基于 GraphRAG 的多跳问答接口（流式返回）；支持配置检索深度与起点实体数。
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    opts = req.options or GraphRAGOptions()
    try:
        return StreamingResponse(
            stream_chat_rag(
                project_id,
                req.query,
                max_degree=opts.max_degree,
                max_start_entities=opts.max_start_entities,
                retrieval_mode=opts.retrieval_mode,
                session_id=req.session_id or DEFAULT_SESSION,
            ),
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.error(f"GraphRAG chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project_id}/graph-rag/chat")
async def get_chat_history(project_id: str, session_id: str = Query(default=DEFAULT_SESSION, description="会话标识")):
    """
    获取问图多轮对话历史（按会话隔离）
    """
    try:
        return {"history": load_history(project_id, session_id)}
    except Exception as e:
        logger.error(f"Failed to load GraphRAG history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{project_id}/graph-rag/chat")
async def delete_chat_history(project_id: str, session_id: str = Query(default=DEFAULT_SESSION, description="会话标识")):
    """
    清除问图多轮对话历史（按会话隔离）
    """
    try:
        clear_history(project_id, session_id)
        return {"message": "History cleared"}
    except Exception as e:
        logger.error(f"Failed to clear GraphRAG history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
