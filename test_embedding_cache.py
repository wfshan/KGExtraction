import asyncio
import numpy as np

from backend.services.embedding import embed_texts, _cache
import os
import sys

# Set path so that backend module is found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))

async def test_cache():
    texts = ["这是一个测试实体1", "这是另一个测试实体2", "这是一个测试实体1"]
    
    # 强制使用 API 方式测试缓存逻辑（如果本地有配置会走API_Fallback或者本地模型）
    print("开始测试 embed_texts()...")
    res = embed_texts(texts)
    print(f"返回形状: {res.shape}")
    print(f"缓存数量: {len(_cache)}，期望应该是2个")
    
if __name__ == "__main__":
    asyncio.run(test_cache())
