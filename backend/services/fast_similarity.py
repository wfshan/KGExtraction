"""
轻量相似度服务（默认替代向量检索）。
组合关键词、字符 n-gram、rapidfuzz（可选）与编辑距离进行打分。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Sequence, Tuple

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz  # type: ignore
except Exception:  # pragma: no cover
    rapidfuzz_fuzz = None


def use_vector_similarity(config: Any) -> bool:
    """统一判定是否启用向量链路。"""
    return str(getattr(config, "similarity_backend", "keyword")).lower() == "vector"


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower().strip()
    return re.sub(r"\s+", "", lowered)


def _keyword_tokens(text: str) -> List[str]:
    if not text:
        return []
    base_tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    tokens: List[str] = []
    for token in base_tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) <= 4:
            tokens.extend(list(token))
        else:
            tokens.append(token)
    return [t for t in tokens if t]


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return set()
    if len(normalized) < n:
        return {normalized}
    return {normalized[i : i + n] for i in range(len(normalized) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _edit_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    dist = _edit_distance(a, b)
    return max(0.0, 1.0 - dist / max_len)


def name_similarity_score(query: str, candidate: str) -> float:
    """适合实体名等短文本的相似度。"""
    q = _normalize_text(query)
    c = _normalize_text(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0

    q_kw = set(_keyword_tokens(q))
    c_kw = set(_keyword_tokens(c))
    q_ng = _char_ngrams(q, 2)
    c_ng = _char_ngrams(c, 2)

    kw_score = _jaccard(q_kw, c_kw)
    ng_score = _jaccard(q_ng, c_ng)
    seq_score = SequenceMatcher(None, q, c).ratio()
    edit_score = _edit_similarity(q[:64], c[:64])
    rapid_score = 0.0
    if rapidfuzz_fuzz is not None:
        rapid_score = rapidfuzz_fuzz.ratio(q, c) / 100.0

    score = (
        0.30 * seq_score
        + 0.25 * ng_score
        + 0.20 * kw_score
        + 0.15 * edit_score
        + 0.10 * rapid_score
    )
    if len(q) >= 2 and (q in c or c in q):
        score = max(score, 0.88)
    return min(1.0, score)


def chunk_similarity_score(query: str, content: str) -> float:
    """适合查询语句 vs 文本片段的相似度。"""
    q = _normalize_text(query)
    c = _normalize_text(content)
    if not q or not c:
        return 0.0

    c_short = c[:600]
    q_tokens = set(_keyword_tokens(q))
    c_tokens = set(_keyword_tokens(c_short))
    token_hit = 0.0
    if q_tokens:
        token_hit = len(q_tokens & c_tokens) / len(q_tokens)

    ng_score = _jaccard(_char_ngrams(q, 2), _char_ngrams(c_short, 2))
    seq_score = SequenceMatcher(None, q[:128], c_short[:256]).ratio()
    rapid_score = 0.0
    if rapidfuzz_fuzz is not None:
        rapid_score = rapidfuzz_fuzz.partial_ratio(q, c_short) / 100.0

    score = 0.45 * token_hit + 0.25 * ng_score + 0.20 * rapid_score + 0.10 * seq_score
    if q in c_short:
        score += 0.15
    return min(1.0, score)


def rank_entity_candidates(
    query: str,
    entities: Sequence[Dict[str, Any]],
    top_k: int = 20,
    score_threshold: float = 0.3,
    preferred_type: str | None = None,
) -> List[Tuple[Dict[str, Any], float]]:
    scored: List[Tuple[Dict[str, Any], float]] = []
    for entity in entities:
        name = str(entity.get("name", ""))
        if not name:
            continue
        score = name_similarity_score(query, name)
        if preferred_type and entity.get("entity_type") == preferred_type:
            score = min(1.0, score + 0.03)
        if score >= score_threshold:
            scored.append((entity, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def rank_chunk_candidates(
    query: str,
    chunks: Sequence[Dict[str, Any]],
    top_k: int = 10,
    score_threshold: float = 0.15,
) -> List[Tuple[Dict[str, Any], float]]:
    scored: List[Tuple[Dict[str, Any], float]] = []
    for chunk in chunks:
        content = str(chunk.get("content", chunk.get("text", "")))
        if not content:
            continue
        score = chunk_similarity_score(query, content)
        if score >= score_threshold:
            scored.append((chunk, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
