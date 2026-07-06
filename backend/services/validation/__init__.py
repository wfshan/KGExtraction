"""确定性校验层（Cognitive-Executive Separation）。

认知层（LLM）只负责提案知识；执行层（本模块）以确定性规则裁决，
不依赖任何 LLM，避免「用 LLM 检查 LLM」的递归信任问题。
"""
from services.validation.gate import (
    ValidationViolation,
    ValidationReport,
    validate_for_publish,
)

__all__ = [
    "ValidationViolation",
    "ValidationReport",
    "validate_for_publish",
]
