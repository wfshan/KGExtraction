"""Schema 约束的统一读取工具。

RelationType 的 source/target 约束支持三种形态：
- ""（空字符串）或 [] → 不约束
- "类型A"             → 单一类型约束
- ["类型A", "类型B"]  → 多类型约束（满足其一即可）

所有校验/裁剪/prompt 渲染逻辑必须经由本模块解析约束，
避免各处对字段形态做出不一致的假设。
"""
from typing import Dict, List, Optional, Set, Union


def constraint_types(raw: Union[str, List[str], None]) -> Set[str]:
    """将约束字段规整为类型集合；空集合表示不约束。"""
    if not raw:
        return set()
    if isinstance(raw, str):
        return {raw} if raw.strip() else set()
    return {t.strip() for t in raw if isinstance(t, str) and t.strip()}


def relation_source_types(rt: Dict) -> Set[str]:
    return constraint_types(rt.get("source_entity_type"))


def relation_target_types(rt: Dict) -> Set[str]:
    return constraint_types(rt.get("target_entity_type"))


def type_satisfies(entity_type: str, constraint: Set[str]) -> bool:
    """实体类型是否满足约束（空约束恒真）。"""
    return not constraint or entity_type in constraint


def format_constraint(raw: Union[str, List[str], None]) -> str:
    """将约束渲染为 prompt/报错可读的文本。"""
    types = sorted(constraint_types(raw))
    if not types:
        return "*"
    return "/".join(types)
