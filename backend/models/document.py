"""
文档数据模型
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid


class Document(BaseModel):
    """文档模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="文档ID")
    project_id: str = Field(..., description="所属项目ID")
    filename: str = Field(..., description="文件名")
    original_filename: str = Field(..., description="原始文件名")
    file_size: int = Field(default=0, description="文件大小(字节)")
    file_type: str = Field(..., description="文件类型: pdf/txt/md/docx/csv")
    upload_time: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="上传时间"
    )
    status: str = Field(default="uploaded", description="状态: uploaded/parsing/parsed/error")
    chunk_count: int = Field(default=0, description="分片数量")
    text_length: int = Field(default=0, description="文本总字符数")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    
    # 分片配置
    chunk_method: str = Field(default="fixed_length", description="切分策略: fixed_length/recursive_character/paragraph/hierarchical")
    chunk_size: int = Field(default=500, description="最大分片长度")
    chunk_overlap: int = Field(default=100, description="分片重叠长度")
    hierarchical_level: int = Field(default=1, description="层级切分的深度(1-6)")
    max_chunk_length: int = Field(default=0, description="分片后的最大块字符长度")
    
    # 抽取配置
    target_entities: List[str] = Field(default_factory=list, description="专属抽取目标实体类型")
    target_relations: List[str] = Field(default_factory=list, description="专属抽取目标关系类型")
