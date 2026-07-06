"""
系统配置路由
"""
from fastapi import APIRouter
from config import load_config, save_config, SystemConfig

router = APIRouter()


@router.get("/config", response_model=SystemConfig)
async def get_system_config():
    """获取系统配置"""
    config = load_config()
    # 隐藏 API Key 中间部分
    if config.api_key and len(config.api_key) > 8:
        masked = config.api_key[:4] + "*" * (len(config.api_key) - 8) + config.api_key[-4:]
        config.api_key = masked
    return config


@router.put("/config", response_model=SystemConfig)
async def update_system_config(config: SystemConfig):
    """更新系统配置"""
    # 如果传入的是掩码 key，保留原始值
    current = load_config()
    if "*" in config.api_key:
        config.api_key = current.api_key
    save_config(config)
    return config
