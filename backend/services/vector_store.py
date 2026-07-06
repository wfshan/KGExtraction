"""
FAISS 向量索引管理
支持创建/加载/保存/搜索/增量更新
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

logger = logging.getLogger(__name__)


class VectorStore:
    """基于 FAISS 的向量存储"""

    def __init__(self, project_dir: Path, name: str = "vector"):
        self.project_dir = project_dir
        self.index_name = name
        self.index_path = project_dir / f"{name}.index"
        self.meta_path = project_dir / f"{name}_meta.json"
        self._index = None
        self._metadata: List[Dict] = []  # [{id, ...}]
        self._new_count = 0  # 新增计数（用于判断是否需要重建）

    def _ensure_faiss(self):
        """确保 FAISS 可用"""
        import faiss
        return faiss

    def _load_index(self):
        """加载已有索引"""
        if self._index is not None:
            return

        faiss = self._ensure_faiss()

        if self.index_path.exists():
            self._index = faiss.read_index(str(self.index_path))
            if self.meta_path.exists():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self._metadata = json.load(f)
            logger.info(f"加载索引: {self._index.ntotal} 条向量")
        else:
            # 创建新索引
            from services.embedding import get_dimension
            dim = get_dimension()
            self._index = faiss.IndexFlatIP(dim)  # 内积 = 归一化后的余弦相似度
            self._metadata = []
            logger.info(f"创建新索引，维度: {dim}")

    def add(self, vectors: np.ndarray, metadata_list: List[Dict]):
        """
        添加向量到索引

        Args:
            vectors: (n, dim) 归一化向量
            metadata_list: 对应的元数据列表
        """
        self._load_index()

        if len(vectors) != len(metadata_list):
            raise ValueError("向量数与元数据数不匹配")

        # 归一化
        import faiss
        faiss.normalize_L2(vectors.astype(np.float32))

        self._index.add(vectors.astype(np.float32))
        self._metadata.extend(metadata_list)
        self._new_count += len(vectors)

        # 保存
        self._save()

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        score_threshold: float = 0.5,
    ) -> List[Tuple[Dict, float]]:
        """
        搜索最相似的向量

        Args:
            query_vector: 查询向量 (dim,)
            top_k: 返回数量
            score_threshold: 最低相似度阈值

        Returns:
            [(metadata, score), ...]
        """
        self._load_index()

        if self._index.ntotal == 0:
            return []

        import faiss
        # 归一化查询向量
        query = query_vector.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(query)

        # 搜索
        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(query, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            if score < score_threshold:
                continue
            results.append((self._metadata[idx], float(score)))

        return results

    def delete_by_metadata(self, key: str, value: Any):
        """根据元数据删除向量（简单实现：重建索引）"""
        self._load_index()
        new_metadata = []
        keep_indices = []
        for i, meta in enumerate(self._metadata):
            if meta.get(key) != value:
                new_metadata.append(meta)
                keep_indices.append(i)
        
        if len(new_metadata) == len(self._metadata):
            return # 无需删除
            
        import faiss
        if not keep_indices:
            self.clear()
            return
            
        # 重建索引
        # 获取所有向量
        # 注意：IndexFlatIP 不支持直接删除，所以我们提取保留的向量并重建
        # 如果是较大的 Index，这会很慢
        all_vectors = []
        for idx in keep_indices:
            vec = self._index.reconstruct(idx)
            all_vectors.append(vec)
            
        dim = self._index.d
        self._index = faiss.IndexFlatIP(dim)
        if all_vectors:
            self.add(np.array(all_vectors), new_metadata)
        else:
            self._metadata = []
            self._save()

    def _save(self):
        """持久化索引和元数据"""
        import faiss
        faiss.write_index(self._index, str(self.index_path))
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, ensure_ascii=False, indent=2)

    def clear(self):
        """清空索引"""
        self._index = None
        self._metadata = []
        self._new_count = 0
        if self.index_path.exists():
            self.index_path.unlink()
        if self.meta_path.exists():
            self.meta_path.unlink()

    @property
    def total_count(self) -> int:
        """当前向量总数"""
        self._load_index()
        return self._index.ntotal
