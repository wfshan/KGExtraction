"""
项目数据模型
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class ProjectCreate(BaseModel):
    """创建项目请求"""
    name: str = Field(..., min_length=1, max_length=100, description="项目名称")
    description: str = Field(default="", max_length=500, description="项目描述")


class Project(BaseModel):
    """项目模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="项目ID")
    name: str = Field(..., description="项目名称")
    description: str = Field(default="", description="项目描述")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间"
    )
    status: str = Field(default="idle", description="状态: idle/running/completed")
    document_count: int = Field(default=0, description="文档数量")
    run_count: int = Field(default=0, description="任务数量")
