"""
抽取任务(Run)数据模型
"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator
import uuid


class RunCreate(BaseModel):
    """创建运行请求"""
    description: str = Field(default="", description="运行描述")


class Run(BaseModel):
    """抽取任务运行模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="运行ID")
    project_id: str = Field(..., description="所属项目ID")
    status: str = Field(default="pending", description="状态: pending/running/completed/failed/rejected")
    progress: float = Field(default=0.0, description="进度百分比 0-100")
    current_step: str = Field(default="", description="当前步骤描述")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="最后一次进度更新时间"
    )
    completed_at: Optional[str] = Field(default=None, description="完成时间")
    description: str = Field(default="", description="运行描述")
    stats: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_chunks": 0,
            "processed_chunks": 0,
            "entities_extracted": 0,
            "relations_extracted": 0,
            "entities_deduplicated": 0,
            "tokens_used": 0,
            "timing_extract_ms": 0.0,
            "timing_disambiguation_ms": 0.0,
            "timing_relations_ms": 0.0,
            "timing_self_correction_ms": 0.0,
            "timing_total_chunk_ms": 0.0,
        },
        description="统计信息"
    )
    error_message: Optional[str] = Field(default=None, description="错误信息")

    @model_validator(mode="before")
    @classmethod
    def set_missing_updated_at(cls, data: Any) -> Any:
        if isinstance(data, dict) and "updated_at" not in data:
            data["updated_at"] = data.get("created_at", datetime.now().isoformat())
        return data
