"""LLM token 用量追踪与预算控制。

- llm_gateway 每次调用完成后上报 usage；
- 按 (project_id, run_id) 聚合（复用 extraction_logger 的 contextvar run 上下文）；
- 抽取流水线在处理每个分片前检查预算，超限则优雅停止。

成本可预估、可控是产品级流水线的基本要求。
"""
import threading
from typing import Dict, Optional, Tuple

from services.extraction_logger import current_run_context

_lock = threading.Lock()
# {(project_id, run_id): {"total_tokens": int, "prompt_tokens": int, "completion_tokens": int, "calls": int}}
_usage: Dict[Tuple[str, str], Dict[str, int]] = {}


def report_usage(usage: Optional[Dict]):
    """由 llm_gateway 调用：将一次 LLM 调用的 usage 归入当前 run 上下文。"""
    project_id, run_id = current_run_context.get()
    if not project_id or not run_id:
        return
    key = (project_id, run_id)
    with _lock:
        entry = _usage.setdefault(key, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
        entry["calls"] += 1
        if usage:
            entry["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
            entry["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            entry["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)


def get_usage(project_id: str, run_id: str) -> Dict[str, int]:
    with _lock:
        return dict(_usage.get((project_id, run_id), {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0}))


def budget_exceeded(project_id: str, run_id: str, budget_tokens: int) -> bool:
    """预算为 0 或负数表示不限制。"""
    if budget_tokens <= 0:
        return False
    return get_usage(project_id, run_id)["total_tokens"] >= budget_tokens


def reset_usage(project_id: str, run_id: str):
    with _lock:
        _usage.pop((project_id, run_id), None)


def estimate_run_cost(total_chunks: int, config, has_inductive_types: bool = False) -> Dict:
    """启动前的确定性成本预估（不调用任何 LLM）。

    每分片调用次数 = 抽取(1 或 2) + 消歧(≤1) + 跨片段(≤1) + 自我修正(≤1)。
    Schema 含归纳类型（inductive）时走归纳分道，每分片额外 +2 次调用
    （归纳抽取 + 忠实度校验），且归纳用强力模型，实际费用更高——预估中单列出来供用户感知。
    token 估算 = 分片长度 + prompt 开销（Schema/指令 ≈ 1200 字符）折算 token（中文 ≈ 1 字/`token`
    的保守近似取 0.8 字/token），输出按每次调用 ≈ 600 token 估。
    这是量级估计，用于用户决策，不是计费依据。
    """
    calls_per_chunk = 1 if getattr(config, "extraction_mode", "one-pass") == "one-pass" else 2
    if getattr(config, "enable_disambiguation", False):
        calls_per_chunk += 1
    if getattr(config, "enable_cross_chunk_inference", False):
        calls_per_chunk += 1
    if getattr(config, "enable_self_correction", False):
        calls_per_chunk += 1

    inductive_calls_per_chunk = 2 if has_inductive_types else 0
    calls_per_chunk += inductive_calls_per_chunk

    chunk_size = int(getattr(config, "chunk_size", 500))
    prompt_overhead_chars = 1200
    input_tokens_per_call = int((chunk_size + prompt_overhead_chars) / 0.8)
    output_tokens_per_call = 600

    total_calls = total_chunks * calls_per_chunk
    est_input = total_calls * input_tokens_per_call
    est_output = total_calls * output_tokens_per_call
    est_total = est_input + est_output

    price_in = float(getattr(config, "price_per_1k_input_tokens", 0.0) or 0.0)
    price_out = float(getattr(config, "price_per_1k_output_tokens", 0.0) or 0.0)
    est_cost = None
    if price_in > 0 or price_out > 0:
        est_cost = round(est_input / 1000 * price_in + est_output / 1000 * price_out, 4)

    budget = int(getattr(config, "run_token_budget", 0) or 0)
    return {
        "total_chunks": total_chunks,
        "calls_per_chunk": calls_per_chunk,
        "inductive_calls_per_chunk": inductive_calls_per_chunk,
        "estimated_inductive_calls": total_chunks * inductive_calls_per_chunk,
        "estimated_calls": total_calls,
        "estimated_input_tokens": est_input,
        "estimated_output_tokens": est_output,
        "estimated_total_tokens": est_total,
        "estimated_cost": est_cost,
        "token_budget": budget,
        "budget_sufficient": (budget <= 0) or (est_total <= budget),
    }
