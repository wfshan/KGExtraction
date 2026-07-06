"""证据确定性验证。

原则：证据必须可验证。LLM 返回的 evidence 短句只有逐字（忽略空白差异）
命中原文 chunk 时才算「已验证」（verified=True）。未命中的证据保留但降级
（verified=False），供审计发现幻觉证据；发布门控只认可已验证证据。

这是一次廉价的确定性字符串匹配——把「可溯源」从口号变成不变量。
"""
import re
from typing import Dict, List, Optional


_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """归一化：去除所有空白（中文文本安全），保留其余字符。"""
    return _WS_RE.sub("", text or "")


def verify_quote(quote: str, chunk_text: str) -> bool:
    """判定 quote 是否逐字出现在 chunk_text 中（忽略空白差异）。"""
    q = _norm(quote)
    if not q:
        return False
    return q in _norm(chunk_text)


def build_evidence(
    chunk_id: str,
    raw_evidence,
    chunk_text: str,
    enabled: bool,
    max_quote_chars: int = 300,
) -> List[Dict]:
    """将 LLM 返回的 evidence 规整为带 verified 标记的 evidence_quotes 列表。"""
    if not enabled or not raw_evidence:
        return []
    quotes: List[str] = []
    if isinstance(raw_evidence, str):
        quotes = [raw_evidence]
    elif isinstance(raw_evidence, list):
        quotes = [str(q) for q in raw_evidence if q]

    result = []
    for q in quotes:
        q = q.strip()
        if not q:
            continue
        result.append({
            "chunk_id": chunk_id,
            "quote": q[:max_quote_chars],
            "verified": verify_quote(q[:max_quote_chars], chunk_text),
        })
    return result


def has_verified_evidence(evidence_quotes: Optional[List[Dict]]) -> bool:
    """是否存在已验证证据。verified 缺失（旧数据、未开启校验时代的数据）视同通过，
    显式 False（已确认未命中原文）不通过。"""
    if not evidence_quotes:
        return False
    for ev in evidence_quotes:
        v = ev.get("verified")
        if v is True or v is None:
            return True
    return False
