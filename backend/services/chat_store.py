"""
GraphRAG 聊天历史记录存储服务
提供基于 JSON 文件的多轮对话持久化和压缩功能
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from config import get_project_dir
from services.llm_gateway import llm_gateway, COMPLEXITY_SIMPLE

logger = logging.getLogger(__name__)

# 定义常量
MAX_HISTORY_TURNS = 5  # 最多保留 5 轮（即 10 条消息：5次 user + 5次 assistant）
DEFAULT_SESSION = "default"


def _sanitize_session_id(session_id: str) -> str:
    """会话 ID 只允许安全字符，防止路径穿越。"""
    sid = "".join(c for c in (session_id or DEFAULT_SESSION) if c.isalnum() or c in "-_")[:64]
    return sid or DEFAULT_SESSION


def _history_path(project_id: str, session_id: str = DEFAULT_SESSION) -> Path:
    """获取会话聊天记录文件路径（按 session 分文件，多会话/多用户互不污染）"""
    sid = _sanitize_session_id(session_id)
    return get_project_dir(project_id) / "chat_sessions" / f"{sid}.json"


def _legacy_history_path(project_id: str) -> Path:
    """旧版项目级单文件历史（仅 default 会话读取时兼容）"""
    return get_project_dir(project_id) / "chat_history.json"


def load_history(project_id: str, session_id: str = DEFAULT_SESSION) -> List[Dict[str, str]]:
    """加载聊天历史"""
    path = _history_path(project_id, session_id)
    # 兼容迁移：default 会话且新文件不存在时，回退读旧的项目级单文件
    if not path.exists() and _sanitize_session_id(session_id) == DEFAULT_SESSION:
        path = _legacy_history_path(project_id)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load chat history for project {project_id}: {e}")
        return []

def save_history(project_id: str, history: List[Dict[str, str]], session_id: str = DEFAULT_SESSION):
    """保存聊天历史"""
    path = _history_path(project_id, session_id)
    try:
        # 确保目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save chat history for project {project_id}: {e}")

def clear_history(project_id: str, session_id: str = DEFAULT_SESSION):
    """清空会话聊天历史（default 会话同时清除旧版单文件）"""
    path = _history_path(project_id, session_id)
    if path.exists():
        path.unlink()
    if _sanitize_session_id(session_id) == DEFAULT_SESSION:
        legacy = _legacy_history_path(project_id)
        if legacy.exists():
            legacy.unlink()

async def add_message(project_id: str, role: str, content: str, session_id: str = DEFAULT_SESSION):
    """
    添加一条新消息到历史记录
    如果历史记录超过阈值，提取最旧的一轮并进行压缩总结
    """
    history = load_history(project_id, session_id)
    history.append({"role": role, "content": content})
    
    # 因为是一问一答，一轮=2条记录。MAX_HISTORY_TURNS * 2 为只保留的详细记录条数
    # 为了压缩，通常保留第一条作为 system summary，其余为 user/assistant
    # 假设 history 结构： [ {role: system, content: summary}, ...recent_msgs ]
    
    # 检查是否有 system summary
    has_summary = len(history) > 0 and history[0].get("role") == "system"
    detailed_msg_start_idx = 1 if has_summary else 0
    detailed_msgs = history[detailed_msg_start_idx:]
    
    # 只有在 assistant 回复完，形成完整的新一轮时才触发判断
    if role == "assistant" and len(detailed_msgs) > MAX_HISTORY_TURNS * 2:
        # 取出最旧的一轮 (user + assistant) 进行压缩
        # 即 detailed_msgs[0] 和 detailed_msgs[1]
        older_turns_to_compress = detailed_msgs[:2]
        remaining_msgs = detailed_msgs[2:]
        
        # 获取现有的 summary
        current_summary = history[0].get("content", "") if has_summary else ""
        
        # 执行压缩
        new_summary = await _compress_turns(current_summary, older_turns_to_compress)
        
        # 重组 history
        history = [{"role": "system", "content": new_summary}] + remaining_msgs

    save_history(project_id, history, session_id)

async def _compress_turns(current_summary: str, turns_to_compress: List[Dict[str, str]]) -> str:
    """调用 LLM 将旧的对话压缩为简短总结"""
    messages_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in turns_to_compress])
    
    prompt = f"""
    请将以下新的对话内容压缩总结，并与之前的历史总结（如果有）合并为一段简短摘要。
    摘要将作为后续问图对话的长期记忆上下文。压缩时请保留：用户问题与助手回答中基于图谱实体或原文的推理链、关键结论与依据（如提到了哪些实体、关系或原文依据），避免丢失对后续多轮问答有依据作用的信息。

    【之前的历史总结】：
    {current_summary if current_summary else "无"}

    【新的对话内容】：
    {messages_text}
    
    【请输出合并后的最新简短总结】：
    """
    
    messages = [
        {"role": "system", "content": "你是一个问图对话记忆压缩助手，擅长在压缩时保留关键实体、推理链以及基于图谱或原文的结论与依据。"},
        {"role": "user", "content": prompt}
    ]
    
    try:
        result = await llm_gateway.chat(messages, complexity=COMPLEXITY_SIMPLE, max_tokens=300)
        return result.get("content", current_summary).strip()
    except Exception as e:
        logger.error(f"对话压缩失败: {e}")
        # 如果失败，暂时保留老 summary 或仅仅拼接文本
        return current_summary
