"""
Embedding 服务模块
支持本地轻量模型生成向量，带哈希缓存
"""
import hashlib
import logging
from typing import List, Optional, Dict
import numpy as np

logger = logging.getLogger(__name__)

# 全局模型实例与缓存
_model = None
_cache: Dict[str, np.ndarray] = {}
_dimension: int = 512  # bge-small-zh-v1.5 默认维度


def _get_model():
    """延迟加载 Embedding 模型"""
    global _model, _dimension

    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            model_name = "BAAI/bge-small-zh-v1.5"
            logger.info(f"加载 Embedding 模型: {model_name}")
            _model = SentenceTransformer(model_name)
            _dimension = _model.get_sentence_embedding_dimension()
            logger.info(f"模型加载完成，向量维度: {_dimension}")
        except Exception as e:
            logger.warning(f"本地模型加载失败: {e}，将使用 API 方式")
            _model = "api_fallback"

    return _model


def get_dimension() -> int:
    """获取向量维度"""
    _get_model()
    return _dimension


def _text_hash(text: str) -> str:
    """文本哈希"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def embed_texts(texts: List[str]) -> np.ndarray:
    """
    批量生成文本向量

    Args:
        texts: 文本列表

    Returns:
        numpy 数组 (n, dim)
    """
    model = _get_model()

    # 先查缓存
    results = []
    uncached_indices = []
    uncached_texts = []

    for i, text in enumerate(texts):
        h = _text_hash(text)
        if h in _cache:
            results.append((i, _cache[h]))
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    # 批量编码未缓存的文本
    if uncached_texts:
        if model == "api_fallback":
            embeddings = _embed_via_api(uncached_texts)
        else:
            embeddings = model.encode(
                uncached_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        for i, (idx, text) in enumerate(zip(uncached_indices, uncached_texts)):
            h = _text_hash(text)
            _cache[h] = embeddings[i]
            results.append((idx, embeddings[i]))

    # 按原始顺序排列
    results.sort(key=lambda x: x[0])
    return np.array([r[1] for r in results])


def embed_text(text: str) -> np.ndarray:
    """生成单个文本的向量"""
    return embed_texts([text])[0]


def _embed_via_api(texts: List[str]) -> np.ndarray:
    """通过 API 生成 Embedding（备用方案）"""
    from config import load_config
    from openai import OpenAI

    config = load_config()
    client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    global _dimension

    result = client.embeddings.create(
        model=config.model_embedding,
        input=texts,
    )

    embeddings = []
    for item in result.data:
        vec = np.array(item.embedding, dtype=np.float32)
        embeddings.append(vec)

    if embeddings:
        _dimension = len(embeddings[0])

    return np.array(embeddings)


def clear_cache():
    """清空缓存"""
    global _cache
    _cache = {}
