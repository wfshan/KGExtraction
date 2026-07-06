"""
抽取任务上下文日志记录器
使用 contextvars 绑定当前线程/协程的 run_id，将日志输出到各自的 run 日志文件中
"""
import logging
import os
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime
from config import get_project_dir

# 线程上下文变量
current_run_context = ContextVar("current_run_context", default=(None, None))

def set_run_context(project_id: str, run_id: str):
    """设置当前线程的执行上下文"""
    current_run_context.set((project_id, run_id))

def get_run_log_file() -> Path | None:
    """获取当前上下文对应的日志文件路径"""
    project_id, run_id = current_run_context.get()
    if not project_id or not run_id:
        return None
    
    runs_dir = get_project_dir(project_id) / "logs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir / f"{run_id}.log"

def log_extraction(message: str, level: str = "INFO"):
    """
    附带时间戳写入特定抽取 run 的日志文件。
    如果不处于任何 run 上下文中，则退化为普通 print
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] [{level}] {message}"
    
    log_file = get_run_log_file()
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(formatted_msg + "\n")
                f.flush()
        except Exception:
            pass
    else:
        # Fallback for standard console logging
        print(f"[Extraction Log Fallback] {formatted_msg}")

def log_extraction_chunk(chunk: str):
    """
    流式追加执行日志内容（不含时间戳和前缀，用于大模型打字机效果）。
    写入后立即 flush，便于前端轮询时能及时看到新内容。
    """
    log_file = get_run_log_file()
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(chunk)
                f.flush()
        except Exception:
            pass
    else:
        print(chunk, end="", flush=True)


def log_extraction_prompt(messages: list, max_user_chars: int = 1200):
    """
    将本次 LLM 调用的输入（Prompt）摘要写入 run 日志，便于执行详情中看到「输入」内容。
    每条 user 内容截断为 max_user_chars 字符，避免单条过长。
    """
    log_file = get_run_log_file()
    if not log_file or not messages:
        return
    try:
        lines = ["【输入】"]
        for i, m in enumerate(messages):
            role = m.get("role", "unknown")
            content = (m.get("content") or "").strip()
            if len(content) > max_user_chars:
                content = content[:max_user_chars] + "\n... (已截断)"
            lines.append(f"  [{role}]: {content}")
        lines.append("")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.flush()
    except Exception:
        pass
