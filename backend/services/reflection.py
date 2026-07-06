"""反思案例库（OneKE Reflection Agent 模式）。

将人工复核阶段的修改/删除记录持久化为「错误-修正」案例，
在后续抽取时作为 few-shot 提示注入，使系统从人工反馈中持续改进。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import get_project_dir

logger = logging.getLogger(__name__)

MAX_CASES = 200  # 案例库上限，超出后丢弃最旧


def _cases_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "reflection_cases.json"


def load_cases(project_id: str) -> List[Dict]:
    f = _cases_file(project_id)
    if not f.exists():
        return []
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def _save_cases(project_id: str, cases: List[Dict]):
    f = _cases_file(project_id)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(cases[-MAX_CASES:], fp, ensure_ascii=False, indent=2)


def record_case(
    project_id: str,
    kind: str,           # "entity" | "relation"
    action: str,         # "modify" | "delete"
    before: Dict,
    after: Optional[Dict] = None,
    note: str = "",
) -> None:
    """记录一条人工复核案例。失败不影响主流程。"""
    try:
        cases = load_cases(project_id)
        cases.append({
            "kind": kind,
            "action": action,
            "before": before,
            "after": after or {},
            "note": note,
            "ts": datetime.now().isoformat(),
        })
        _save_cases(project_id, cases)
    except Exception as e:
        logger.warning(f"[反思案例] 记录失败: {e}")


def format_cases_for_prompt(project_id: str, max_cases: int = 12) -> str:
    """将最近的人工复核案例格式化为 few-shot 指引文本，供抽取 prompt 注入。"""
    cases = load_cases(project_id)
    if not cases:
        return ""
    # 优先展示删除与修改类型，取最近 max_cases 条
    recent = cases[-max_cases:]
    lines: List[str] = []
    for c in recent:
        kind = c.get("kind")
        action = c.get("action")
        before = c.get("before", {})
        after = c.get("after", {})
        if action == "delete":
            if kind == "entity":
                lines.append(f"- 不要抽取这类实体：「{before.get('name','')}」（类型 {before.get('entity_type','')}）——人工已判定为错误。")
            else:
                lines.append(f"- 不要抽取这类关系：{before.get('relation_type','')}（人工已删除）。")
        elif action == "modify":
            if kind == "entity":
                bt = before.get("entity_type", "")
                at = after.get("entity_type", bt)
                if bt and at and bt != at:
                    lines.append(f"- 实体「{after.get('name', before.get('name',''))}」应归类为「{at}」而非「{bt}」。")
            else:
                bt = before.get("relation_type", "")
                at = after.get("relation_type", bt)
                if bt and at and bt != at:
                    lines.append(f"- 关系「{bt}」更规范的表达是「{at}」。")
    lines = [l for l in lines if l]
    if not lines:
        return ""
    return (
        "\n## 人工复核经验（请严格遵循，避免重复历史错误）\n" + "\n".join(lines[:max_cases]) + "\n"
    )
