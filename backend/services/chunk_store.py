"""
文本片段 (Chunk) 本地 SQLite 存储服务
用于支持 GraphRAG 快速检索原始文本片段
"""
import sqlite3
import threading
from pathlib import Path
from typing import List, Dict, Optional
from config import get_project_dir
import logging

logger = logging.getLogger(__name__)

# 使用 ThreadLocal 存储数据库连接，避免多线程下的 sqlite 报错
_local = threading.local()

def _get_db_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "chunk_store.db"

def _get_connection(project_id: str) -> sqlite3.Connection:
    """获取单例（按线程）的 SQLite 连接"""
    db_path = _get_db_path(project_id)
    
    # 简单的连接池/缓存机制
    if not hasattr(_local, 'connections'):
        _local.connections = {}
        
    if project_id not in _local.connections:
        is_new = not db_path.exists()
        # check_same_thread=False 允许夸线程传递，但我们用 ThreadLocal 隔离以确保安全
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.connections[project_id] = conn
        
        if is_new:
            _init_db(conn)
            
    return _local.connections[project_id]

def _init_db(conn: sqlite3.Connection):
    """初始化数据库表"""
    cursor = conn.cursor()
    # 创建 chunks 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            content TEXT NOT NULL,
            index_num INTEGER
        )
    ''')
    # 创建索引加速查询
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_doc_id ON chunks(doc_id)')
    conn.commit()
    logger.info("Initialized chunk_store.db")

def init_project_db(project_id: str):
    """显式初始化项目对应的数据库"""
    _get_connection(project_id)

def save_chunks(project_id: str, chunks: List[Dict]):
    """
    批量保存 chunks
    chunks 格式期望为: [{'id': 'xxx', 'doc_id': 'xxx', 'text': '...', 'index': 0}, ...]
    """
    if not chunks:
        return
        
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    
    records = []
    for chunk in chunks:
        records.append((
            chunk.get('id'),
            chunk.get('doc_id'),
            chunk.get('text', chunk.get('content', '')),
            chunk.get('index', 0)
        ))
        
    cursor.executemany('''
        INSERT OR REPLACE INTO chunks (chunk_id, doc_id, content, index_num)
        VALUES (?, ?, ?, ?)
    ''', records)
    
    conn.commit()
    logger.info(f"Saved {len(chunks)} chunks to chunk_store.db for project {project_id}")

def get_chunk(project_id: str, chunk_id: str) -> Optional[Dict]:
    """通过 chunk_id 查询单个 chunk 内容"""
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    
    cursor.execute('SELECT chunk_id, doc_id, content, index_num FROM chunks WHERE chunk_id = ?', (chunk_id,))
    row = cursor.fetchone()
    
    if row:
        return dict(row)
    return None

def get_chunks_by_ids(project_id: str, chunk_ids: List[str]) -> List[Dict]:
    """批量通过 chunk_ids 查询 chunks"""
    if not chunk_ids:
        return []
        
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    
    # 构造 IN 语句
    placeholders = ','.join(['?'] * len(chunk_ids))
    query = f'SELECT chunk_id, doc_id, content, index_num FROM chunks WHERE chunk_id IN ({placeholders})'
    
    cursor.execute(query, chunk_ids)
    rows = cursor.fetchall()
    
    return [dict(row) for row in rows]

def get_chunks_by_doc_id(project_id: str, doc_id: str) -> List[Dict]:
    """获取某个文档的所有 chunks"""
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    
    cursor.execute('SELECT chunk_id, doc_id, content, index_num FROM chunks WHERE doc_id = ? ORDER BY index_num', (doc_id,))
    rows = cursor.fetchall()
    
    return [dict(row) for row in rows]

def delete_chunks_by_doc_id(project_id: str, doc_id: str):
    """删除某个文档的所有 chunks"""
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM chunks WHERE doc_id = ?', (doc_id,))
    conn.commit()
    logger.info(f"Deleted chunks for doc_id {doc_id} in project {project_id}")


def list_all_chunks(project_id: str) -> List[Dict]:
    """获取项目下全部 chunks（用于快速检索模式）"""
    conn = _get_connection(project_id)
    cursor = conn.cursor()
    cursor.execute('SELECT chunk_id, doc_id, content, index_num FROM chunks')
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def search_chunks_fast(
    project_id: str,
    query: str,
    top_k: int = 10,
    score_threshold: float = 0.15
) -> List[Dict]:
    """使用关键词 + n-gram + 编辑距离等轻量策略检索 chunk。"""
    from services.fast_similarity import rank_chunk_candidates

    chunks = list_all_chunks(project_id)
    ranked = rank_chunk_candidates(
        query=query,
        chunks=chunks,
        top_k=top_k,
        score_threshold=score_threshold,
    )

    result: List[Dict] = []
    for chunk, score in ranked:
        payload = dict(chunk)
        payload["score"] = float(score)
        result.append(payload)
    return result
