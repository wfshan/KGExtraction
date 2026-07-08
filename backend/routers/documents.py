"""
文档管理路由
"""
import json
import shutil
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from models.document import Document
from config import get_project_dir, load_config
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "docx", "csv", "xlsx"}


def _get_docs_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "documents.json"


def _load_documents(project_id: str) -> List[Document]:
    docs_file = _get_docs_file(project_id)
    if not docs_file.exists():
        return []
    with open(docs_file, "r", encoding="utf-8") as f:
        return [Document(**d) for d in json.load(f)]


def _save_documents(project_id: str, docs: List[Document]):
    docs_file = _get_docs_file(project_id)
    with open(docs_file, "w", encoding="utf-8") as f:
        json.dump([d.model_dump() for d in docs], f, ensure_ascii=False, indent=2)


def _invalidate_schema_profile(project_id: str):
    """项目文档或分片变化时清除缓存的文档背景报告，下次对话配置将基于当前文档重新提炼。"""
    profile_path = get_project_dir(project_id) / "schema_profile.md"
    if profile_path.exists():
        try:
            profile_path.unlink()
        except OSError:
            pass

async def _index_chunks_task(project_id: str, doc_id: str, chunks: List[dict]):
    """后台任务：将分片建立向量索引"""
    if not chunks:
        return
    cfg = load_config()
    if str(getattr(cfg, "similarity_backend", "keyword")).lower() != "vector":
        logger.info(f"当前为快速相似度模式，跳过文档 {doc_id} 的向量索引构建")
        return
    try:
        from services.vector_store import VectorStore
        from services.embedding import embed_texts

        project_dir = get_project_dir(project_id)
        v_store = VectorStore(project_dir, name="vector")
        
        # 1. 先清理该文档旧的索引
        v_store.delete_by_metadata("doc_id", doc_id)
        
        # 2. 批量生成向量
        texts = [c.get("text", c.get("content", "")) for c in chunks]
        embeddings = embed_texts(texts)
        
        # 3. 构造元数据并添加
        metas = []
        for i, chunk in enumerate(chunks):
            metas.append({
                "chunk_id": chunk.get("id"),
                "doc_id": doc_id,
                "index": chunk.get("index", i)
            })
        
        v_store.add(embeddings, metas)
        logger.info(f"成功为文档 {doc_id} 的 {len(chunks)} 个分片建立向量语义索引")
    except Exception as e:
        logger.error(f"后台索引分片失败 ({doc_id}): {e}")


@router.post("/{project_id}/documents", response_model=Document)
async def upload_document(project_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """上传文档"""
    # 检查文件扩展名
    filename = file.filename or "unknown.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: .{ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 保存文件
    project_dir = get_project_dir(project_id)
    docs_dir = project_dir / "documents"
    docs_dir.mkdir(exist_ok=True)

    doc = Document(
        project_id=project_id,
        filename="",
        original_filename=filename,
        file_type=ext,
    )
    # 上传时同步应用当前系统分片参数，避免首次处理仍走模型默认值
    cfg = load_config()
    doc.chunk_size = cfg.chunk_size
    doc.chunk_overlap = cfg.chunk_overlap
    # 使用 doc.id 作为存储文件名，避免冲突
    stored_name = f"{doc.id}.{ext}"
    doc.filename = stored_name

    file_path = docs_dir / stored_name
    content = await file.read()
    doc.file_size = len(content)
    with open(file_path, "wb") as f:
        f.write(content)

    # 触发文档解析（异步）
    from services.parser import parse_document
    from services.chunker import chunk_text

    try:
        doc.status = "parsing"
        text = parse_document(file_path, ext)
        doc.text_length = len(text)

        # 保存解析后的纯文本
        text_file = project_dir / "chunks" / f"{doc.id}_raw.txt"
        text_file.parent.mkdir(exist_ok=True)
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(text)

        # 文本分片
        chunks = chunk_text(
            text=text,
            doc_id=doc.id,
            chunk_method=doc.chunk_method,
            chunk_size=doc.chunk_size,
            chunk_overlap=doc.chunk_overlap,
        )
        doc.chunk_count = len(chunks)

        # 保存分片数据到 JSON （为了向后兼容）
        chunks_file = project_dir / "chunks" / f"{doc.id}_chunks.json"
        with open(chunks_file, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

        # 同时保存到 SQLite 的 chunk_store
        from services.chunk_store import save_chunks
        save_chunks(project_id, chunks)

        # 异步建立向量索引
        if str(getattr(cfg, "similarity_backend", "keyword")).lower() == "vector":
            background_tasks.add_task(_index_chunks_task, project_id, doc.id, chunks)

        doc.status = "parsed"
    except Exception as e:
        doc.status = "error"
        doc.error_message = str(e)

    # 保存文档记录
    docs = _load_documents(project_id)
    docs.append(doc)
    _save_documents(project_id, docs)

    # 项目文档有变化，清除已缓存的文档背景报告，下次对话配置将重新提炼
    _invalidate_schema_profile(project_id)
    return doc


@router.get("/{project_id}/documents", response_model=List[Document])
async def list_documents(project_id: str):
    """获取文档列表"""
    return _load_documents(project_id)


@router.delete("/{project_id}/documents/{doc_id}")
async def delete_document(project_id: str, background_tasks: BackgroundTasks, doc_id: str):
    """删除文档"""
    docs = _load_documents(project_id)
    found = False
    new_docs = []
    for d in docs:
        if d.id == doc_id:
            found = True
            # 删除文件
            project_dir = get_project_dir(project_id)
            file_path = project_dir / "documents" / d.filename
            if file_path.exists():
                file_path.unlink()
            # 删除解析文件
            raw_file = project_dir / "chunks" / f"{d.id}_raw.txt"
            if raw_file.exists():
                raw_file.unlink()
            chunks_file = project_dir / "chunks" / f"{d.id}_chunks.json"
            if chunks_file.exists():
                chunks_file.unlink()
            # 从 SQLite 中删除
            from services.chunk_store import delete_chunks_by_doc_id
            delete_chunks_by_doc_id(project_id, d.id)
            
            # 从向量索引中删除
            cfg = load_config()
            if str(getattr(cfg, "similarity_backend", "keyword")).lower() == "vector":
                from services.vector_store import VectorStore
                v_store = VectorStore(get_project_dir(project_id), name="vector")
                v_store.delete_by_metadata("doc_id", d.id)
        else:
            new_docs.append(d)

    if not found:
        raise HTTPException(status_code=404, detail="文档不存在")

    _save_documents(project_id, new_docs)
    # 项目文档有变化，清除已缓存的文档背景报告，下次对话配置将重新提炼
    _invalidate_schema_profile(project_id)
    return {"message": "文档已删除"}


class DocumentUpdate(BaseModel):
    """文档更新请求模型"""
    target_entities: Optional[List[str]] = None
    target_relations: Optional[List[str]] = None


@router.patch("/{project_id}/documents/{doc_id}", response_model=Document)
async def update_document(project_id: str, doc_id: str, updates: DocumentUpdate):
    """更新文档配置（例如专属抽取目标）"""
    docs = _load_documents(project_id)
    doc_to_update = None
    for d in docs:
        if d.id == doc_id:
            if updates.target_entities is not None:
                d.target_entities = updates.target_entities
            if updates.target_relations is not None:
                d.target_relations = updates.target_relations
            doc_to_update = d
            break

    if not doc_to_update:
        raise HTTPException(status_code=404, detail="文档不存在")

    _save_documents(project_id, docs)
    return doc_to_update


class RechunkRequest(BaseModel):
    chunk_method: str
    chunk_size: int = 500
    chunk_overlap: int = 100
    hierarchical_level: int = 1

@router.post("/{project_id}/documents/{doc_id}/rechunk", response_model=Document)
async def rechunk_document(project_id: str, doc_id: str, req: RechunkRequest, background_tasks: BackgroundTasks):
    """根据新的切分策略重新处理文档分片"""
    docs = _load_documents(project_id)
    doc = None
    for d in docs:
        if d.id == doc_id:
            doc = d
            break

    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    project_dir = get_project_dir(project_id)
    text_file = project_dir / "chunks" / f"{doc.id}_raw.txt"
    if not text_file.exists():
        raise HTTPException(status_code=404, detail="找不到该文档的原始文本数据，无法重新分片")

    with open(text_file, "r", encoding="utf-8") as f:
        text = f.read()

    from services.chunker import chunk_text
    try:
        # 1. 重新分片
        # 对于层级切分，Markdown 需要原始标记 (#) 来识别层级，所以读原始文件
        # 但 DOCX/PDF 原始文件是二进制，必须使用已解析的纯文本 (text 变量已在上方读取)
        if req.chunk_method == "hierarchical" and doc.file_type.lower() in ["md", "markdown"]:
            original_file = project_dir / "documents" / doc.filename
            if original_file.exists():
                with open(original_file, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()

        chunks = chunk_text(
            text=text, 
            doc_id=doc.id, 
            chunk_method=req.chunk_method, 
            chunk_size=req.chunk_size, 
            chunk_overlap=req.chunk_overlap,
            hierarchical_level=req.hierarchical_level,
            file_type=doc.file_type
        )
        
        # 2. 更新元数据
        doc.chunk_method = req.chunk_method
        doc.chunk_size = req.chunk_size
        doc.chunk_overlap = req.chunk_overlap
        doc.hierarchical_level = req.hierarchical_level
        doc.chunk_count = len(chunks)
        
        # 计算最大块长
        if chunks:
            doc.max_chunk_length = max(len(c.get("text", "")) for c in chunks)
        else:
            doc.max_chunk_length = 0

        # 3. 覆盖写入本地存储
        chunks_file = project_dir / "chunks" / f"{doc.id}_chunks.json"
        with open(chunks_file, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

        # 4. 覆盖写入 SQLite
        from services.chunk_store import save_chunks, delete_chunks_by_doc_id
        delete_chunks_by_doc_id(project_id, doc.id)
        if chunks: # 只有在有结果集时才保存
            save_chunks(project_id, chunks)
            # 异步更新向量索引
            cfg = load_config()
            if str(getattr(cfg, "similarity_backend", "keyword")).lower() == "vector":
                background_tasks.add_task(_index_chunks_task, project_id, doc.id, chunks)

        # 5. 回写 _save_documents
        _save_documents(project_id, docs)
        # 分片变化后文档“形态”变化，清除缓存的文档背景报告
        _invalidate_schema_profile(project_id)
        return doc
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"重新分片失败: {str(e)}")

# ===== 片段内容查询 =====

class ChunkQueryRequest(BaseModel):
    """片段查询请求"""
    chunk_ids: List[str]


class ChunkContent(BaseModel):
    """片段内容"""
    id: str
    doc_id: str
    text: str
    index: int
    metadata: Optional[dict] = None


@router.get("/{project_id}/documents/{doc_id}/chunks", response_model=List[ChunkContent])
async def list_document_chunks(project_id: str, doc_id: str):
    """获取指定文档的所有分片"""
    project_dir = get_project_dir(project_id)
    chunks_file = project_dir / "chunks" / f"{doc_id}_chunks.json"
    if not chunks_file.exists():
        return []
    
    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    
    return [
        ChunkContent(
            id=c["id"],
            doc_id=c["doc_id"],
            text=c["text"],
            index=c["index"],
            metadata=c.get("metadata", {})
        ) for c in chunks
    ]


@router.post("/{project_id}/chunks", response_model=List[ChunkContent])
async def get_chunks_by_ids(project_id: str, req: ChunkQueryRequest):
    """根据片段 ID 列表获取片段内容"""
    if not req.chunk_ids:
        return []

    # 过滤掉特殊标记（如 cold_start）
    real_ids = {cid for cid in req.chunk_ids if cid != "cold_start"}
    if not real_ids:
        return []

    project_dir = get_project_dir(project_id)
    chunks_dir = project_dir / "chunks"
    if not chunks_dir.exists():
        return []

    results = []
    id_set = set(real_ids)

    for chunk_file in chunks_dir.glob("*_chunks.json"):
        with open(chunk_file, "r", encoding="utf-8") as f:
            chunks = json.load(f)
        for chunk in chunks:
            if chunk["id"] in id_set:
                results.append(ChunkContent(
                    id=chunk["id"],
                    doc_id=chunk["doc_id"],
                    text=chunk["text"],
                    index=chunk["index"],
                ))
                id_set.discard(chunk["id"])
        if not id_set:
            break

    return results
