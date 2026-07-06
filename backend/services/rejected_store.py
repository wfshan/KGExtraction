"""被拒项存储：抽取阶段被规则校验丢弃的实体/关系不再静默消失。

原则：LLM 提案 → 规则/人裁决。规则否决的提案必须可见，原因有二：
1. 审计——人可以复查规则是否误杀了有效知识；
2. Schema 演化——out-of-schema 的高频类型正是 Schema 缺口的信号源。

存储在项目 graph.db 的 rejected_items 表中。
"""
import json
import sqlite3
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import get_project_dir


def _db_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "graph.db"


def _ensure_table(conn: sqlite3.Connection):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS rejected_items (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            run_id TEXT,
            chunk_id TEXT,
            kind TEXT NOT NULL,          -- entity | relation
            name TEXT,                   -- 实体名 或 "源->目标"
            item_type TEXT,              -- 实体类型 或 关系类型
            reason TEXT NOT NULL,
            payload TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_items(run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rejected_type ON rejected_items(kind, item_type)')


def add_rejected(project_id: str, items: List[Dict]):
    """批量写入被拒项。item: {run_id, chunk_id, kind, name, item_type, reason, payload}"""
    if not items:
        return
    try:
        conn = sqlite3.connect(str(_db_path(project_id)))
        try:
            _ensure_table(conn)
            conn.executemany(
                "INSERT INTO rejected_items (id, ts, run_id, chunk_id, kind, name, item_type, reason, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(uuid.uuid4()),
                        datetime.now().isoformat(),
                        it.get("run_id", ""),
                        it.get("chunk_id", ""),
                        it.get("kind", ""),
                        it.get("name", ""),
                        it.get("item_type", ""),
                        it.get("reason", ""),
                        json.dumps(it.get("payload", {}), ensure_ascii=False, default=str),
                    )
                    for it in items
                ],
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[RejectedStore] 写入被拒项失败: {e}")


def list_rejected(project_id: str, run_id: Optional[str] = None, limit: int = 200) -> List[Dict]:
    db = _db_path(project_id)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        _ensure_table(conn)
        if run_id:
            rows = conn.execute(
                "SELECT ts, run_id, chunk_id, kind, name, item_type, reason, payload FROM rejected_items WHERE run_id = ? ORDER BY ts DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, run_id, chunk_id, kind, name, item_type, reason, payload FROM rejected_items ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        try:
            payload = json.loads(r[7]) if r[7] else {}
        except Exception:
            payload = {}
        result.append({
            "ts": r[0], "run_id": r[1], "chunk_id": r[2], "kind": r[3],
            "name": r[4], "item_type": r[5], "reason": r[6], "payload": payload,
        })
    return result


def rejected_stats(project_id: str) -> Dict:
    """被拒项统计：总量、按原因、按（kind, 类型）的频次——供 Schema 缺口检测使用。"""
    db = _db_path(project_id)
    if not db.exists():
        return {"total": 0, "by_reason": {}, "entity_types": [], "relation_types": []}
    conn = sqlite3.connect(str(db))
    try:
        _ensure_table(conn)
        rows = conn.execute("SELECT kind, item_type, reason, name FROM rejected_items").fetchall()
    finally:
        conn.close()

    by_reason = Counter()
    entity_counter = Counter()
    relation_counter = Counter()
    entity_examples: Dict[str, List[str]] = {}
    relation_examples: Dict[str, List[str]] = {}
    for kind, item_type, reason, name in rows:
        by_reason[reason] += 1
        if kind == "entity" and item_type:
            entity_counter[item_type] += 1
            entity_examples.setdefault(item_type, [])
            if name and len(entity_examples[item_type]) < 5 and name not in entity_examples[item_type]:
                entity_examples[item_type].append(name)
        elif kind == "relation" and item_type:
            relation_counter[item_type] += 1
            relation_examples.setdefault(item_type, [])
            if name and len(relation_examples[item_type]) < 5 and name not in relation_examples[item_type]:
                relation_examples[item_type].append(name)

    return {
        "total": len(rows),
        "by_reason": dict(by_reason),
        "entity_types": [
            {"name": t, "count": c, "examples": entity_examples.get(t, [])}
            for t, c in entity_counter.most_common()
        ],
        "relation_types": [
            {"name": t, "count": c, "examples": relation_examples.get(t, [])}
            for t, c in relation_counter.most_common()
        ],
    }


def clear_rejected(project_id: str, run_id: Optional[str] = None) -> int:
    db = _db_path(project_id)
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        _ensure_table(conn)
        if run_id:
            cur = conn.execute("DELETE FROM rejected_items WHERE run_id = ?", (run_id,))
        else:
            cur = conn.execute("DELETE FROM rejected_items")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
