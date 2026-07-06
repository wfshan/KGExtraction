"""审计日志：记录所有改变图谱状态的操作（谁、何时、做了什么）。

合规/溯源场景的硬需求：发布、驳回、人工增删改、复核裁决都必须留痕。
存储在项目 graph.db 的 audit_log 表中，只追加、不修改。
"""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import get_project_dir


def _db_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "graph.db"


def _ensure_table(conn: sqlite3.Connection):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target_kind TEXT,
            target_id TEXT,
            detail TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)')


def record_audit(
    project_id: str,
    actor: str,
    action: str,
    target_kind: str = "",
    target_id: str = "",
    detail: Optional[Dict[str, Any]] = None,
):
    """追加一条审计记录。审计失败不阻断业务操作（但会打印告警）。"""
    try:
        conn = sqlite3.connect(str(_db_path(project_id)))
        try:
            _ensure_table(conn)
            conn.execute(
                "INSERT INTO audit_log (id, ts, actor, action, target_kind, target_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    datetime.now().isoformat(),
                    actor or "anonymous",
                    action,
                    target_kind,
                    target_id,
                    json.dumps(detail or {}, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[Audit] 记录审计日志失败: {e}")


def list_audit(project_id: str, limit: int = 100, action: Optional[str] = None) -> List[Dict]:
    """按时间倒序读取审计记录。"""
    db = _db_path(project_id)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        _ensure_table(conn)
        if action:
            rows = conn.execute(
                "SELECT id, ts, actor, action, target_kind, target_id, detail FROM audit_log WHERE action = ? ORDER BY ts DESC LIMIT ?",
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, actor, action, target_kind, target_id, detail FROM audit_log ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        try:
            detail = json.loads(r[6]) if r[6] else {}
        except Exception:
            detail = {}
        result.append({
            "id": r[0], "ts": r[1], "actor": r[2], "action": r[3],
            "target_kind": r[4], "target_id": r[5], "detail": detail,
        })
    return result
