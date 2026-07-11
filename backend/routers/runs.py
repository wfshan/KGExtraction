"""
抽取任务路由
"""
import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from models.run import Run, RunCreate
from config import get_project_dir

router = APIRouter()


def _get_runs_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "runs.json"


def _load_runs(project_id: str) -> List[Run]:
    runs_file = _get_runs_file(project_id)
    if not runs_file.exists():
        return []
    with open(runs_file, "r", encoding="utf-8") as f:
        return [Run(**r) for r in json.load(f)]


def _save_runs(project_id: str, runs: List[Run]):
    runs_file = _get_runs_file(project_id)
    with open(runs_file, "w", encoding="utf-8") as f:
        json.dump([r.model_dump() for r in runs], f, ensure_ascii=False, indent=2, default=str)


def _update_run(project_id: str, run_id: str, **updates):
    """更新单个 Run 的字段"""
    runs = _load_runs(project_id)
    for r in runs:
        if r.id == run_id:
            for k, v in updates.items():
                setattr(r, k, v)
            r.updated_at = datetime.now().isoformat()
            break
    _save_runs(project_id, runs)


@router.get("/{project_id}/extraction-plan")
async def get_extraction_plan(project_id: str):
    """返回当前项目的抽取计划（Plan，v3）——由当前 Schema（含抽象度）+ config 编译。"""
    from services.planning import compile_default_plan, validate_plan
    plan = compile_default_plan(project_id)
    ok, errors = validate_plan(plan)
    return {"plan": plan.model_dump(), "valid": ok, "errors": errors}


class PlanSuggestRequest(BaseModel):
    user_intent: str = ""


@router.post("/{project_id}/extraction-plan/suggest")
async def suggest_extraction_plan(project_id: str, req: PlanSuggestRequest = None):
    """规划器（v3 第③步）：LLM 分析 Schema + 文档样本，建议每个类型的抽取语义，
    并给出应用后的预览 Plan。不落库，供前端确认。"""
    from services.planning import suggest_extraction_semantics, compile_preview_plan, validate_plan
    req = req or PlanSuggestRequest()
    try:
        semantics = await suggest_extraction_semantics(project_id, user_intent=req.user_intent)
        preview = compile_preview_plan(project_id, semantics)
        ok, errors = validate_plan(preview)
        return {"semantics": semantics, "preview_plan": preview.model_dump(), "valid": ok, "errors": errors}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class PlanApplyRequest(BaseModel):
    semantics: List[dict]


@router.post("/{project_id}/extraction-plan/preview")
async def preview_extraction_plan(project_id: str, req: PlanApplyRequest):
    """用给定 semantics 编译预览 Plan（不落库）——供前端调整抽象度后实时预览。"""
    from services.planning import compile_preview_plan, validate_plan
    try:
        plan = compile_preview_plan(project_id, req.semantics)
        ok, errors = validate_plan(plan)
        return {"plan": plan.model_dump(), "valid": ok, "errors": errors}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/extraction-plan/apply")
async def apply_extraction_plan(project_id: str, req: PlanApplyRequest):
    """人工确认后：把抽取语义写回 Schema 并返回最终 Plan。"""
    from services.planning import apply_extraction_semantics, compile_default_plan, validate_plan
    try:
        apply_extraction_semantics(project_id, req.semantics)
        plan = compile_default_plan(project_id)
        ok, errors = validate_plan(plan)
        return {"message": "抽取计划已应用", "plan": plan.model_dump(), "valid": ok, "errors": errors}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/runs/estimate")
async def estimate_run(project_id: str):
    """启动前成本预估：按分片数 × 启用功能 × 平均 token 估算调用量与费用（确定性，不调用 LLM）。"""
    from config import load_config
    from services.extraction.graph import _load_all_chunks
    from services.usage_tracker import estimate_run_cost

    project_dir = get_project_dir(project_id)
    total_chunks = len(_load_all_chunks(project_dir))
    if total_chunks == 0:
        raise HTTPException(status_code=400, detail="没有可处理的文档分片，请先上传并解析文档")

    # 归纳类型走额外的归纳+忠实度校验分道（更贵），预估须如实反映
    has_inductive = False
    schema_file = project_dir / "schema.json"
    if schema_file.exists():
        try:
            import json as _json
            with open(schema_file, "r", encoding="utf-8") as f:
                schema = _json.load(f)
            has_inductive = any(
                et.get("abstractness") == "inductive" for et in schema.get("entity_types", [])
            )
        except Exception:
            pass
    return estimate_run_cost(total_chunks, load_config(), has_inductive_types=has_inductive)


@router.post("/{project_id}/runs", response_model=Run)
async def start_run(
    project_id: str,
    req: RunCreate = None,
):
    """启动抽取任务"""
    if req is None:
        req = RunCreate()

    project_dir = get_project_dir(project_id)

    # 检查是否有文档
    docs_file = project_dir / "documents.json"
    if not docs_file.exists():
        raise HTTPException(status_code=400, detail="请先上传文档")

    with open(docs_file, "r", encoding="utf-8") as f:
        docs = json.load(f)
    if not docs:
        raise HTTPException(status_code=400, detail="请先上传文档")

    # 检查是否有 Schema
    schema_file = project_dir / "schema.json"
    if not schema_file.exists():
        raise HTTPException(status_code=400, detail="请先配置 Schema")

    # 检查是否有正在运行的任务
    runs = _load_runs(project_id)
    for r in runs:
        if r.status == "running":
            raise HTTPException(status_code=400, detail="已有任务正在运行")

    # 创建 Run
    run = Run(
        project_id=project_id,
        description=req.description,
        status="running",
    )
    runs.append(run)
    _save_runs(project_id, runs)

    # 在独立线程池中执行抽取，运行 asyncio 事件循环
    import threading
    import asyncio
    
    def _run_in_thread():
        asyncio.run(_execute_extraction_async(project_id, run.id))

    t = threading.Thread(
        target=_run_in_thread,
        daemon=True,
    )
    t.start()

    return run


async def _execute_extraction_async(project_id: str, run_id: str):
    """在独立执行线程的事件循环中异步执行抽取任务"""
    print(f"[Extraction] 开始抽取: project={project_id}, run={run_id}")
    try:
        from services.extraction.graph import run_extraction_pipeline_sync
        from services.planning import compile_default_plan
        # v3：编译默认 Plan（等价于当前 config）后执行——执行路径经过 Plan，行为不变
        plan = compile_default_plan(project_id)
        await run_extraction_pipeline_sync(
            project_id=project_id,
            run_id=run_id,
            progress_callback=lambda **kw: _update_run(project_id, run_id, **kw),
            plan=plan,
        )
        # 全量抽取完成：记录所有已解析文档为已处理（供增量摄入计算 delta）
        try:
            docs_file = get_project_dir(project_id) / "documents.json"
            if docs_file.exists():
                with open(docs_file, "r", encoding="utf-8") as f:
                    docs = json.load(f)
                _save_processed_docs(project_id, [d["id"] for d in docs if d.get("status") == "parsed"])
        except Exception as e:
            print(f"[Extraction] 记录已处理文档失败: {e}")
        _update_run(
            project_id, run_id,
            status="completed",
            progress=100.0,
            current_step="抽取完成",
            completed_at=datetime.now().isoformat(),
        )
        print(f"[Extraction] 抽取完成: run={run_id}")
    except Exception as e:
        print(f"[Extraction] 抽取失败: {e}")
        traceback.print_exc()
        _update_run(
            project_id, run_id,
            status="failed",
            error_message=str(e),
            completed_at=datetime.now().isoformat(),
        )


def _processed_docs_file(project_id: str) -> Path:
    return get_project_dir(project_id) / "processed_docs.json"


def _load_processed_docs(project_id: str) -> List[str]:
    f = _processed_docs_file(project_id)
    if not f.exists():
        return []
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def _save_processed_docs(project_id: str, doc_ids: List[str]):
    f = _processed_docs_file(project_id)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(sorted(set(doc_ids)), fp, ensure_ascii=False, indent=2)


@router.post("/{project_id}/runs/incremental", response_model=Run)
async def start_incremental_run(project_id: str, req: RunCreate = None):
    """增量摄入：仅处理尚未抽取过的（新）文档分片，按名称 delta merge 进既有草稿图谱。"""
    if req is None:
        req = RunCreate()

    project_dir = get_project_dir(project_id)
    docs_file = project_dir / "documents.json"
    if not docs_file.exists():
        raise HTTPException(status_code=400, detail="请先上传文档")
    with open(docs_file, "r", encoding="utf-8") as f:
        docs = json.load(f)

    parsed_doc_ids = [d["id"] for d in docs if d.get("status") == "parsed"]
    processed = set(_load_processed_docs(project_id))
    delta_doc_ids = [d for d in parsed_doc_ids if d not in processed]

    if not delta_doc_ids:
        raise HTTPException(status_code=400, detail="没有检测到新增（未抽取）文档")

    schema_file = project_dir / "schema.json"
    if not schema_file.exists():
        raise HTTPException(status_code=400, detail="请先配置 Schema")

    runs = _load_runs(project_id)
    for r in runs:
        if r.status == "running":
            raise HTTPException(status_code=400, detail="已有任务正在运行")

    run = Run(
        project_id=project_id,
        description=(req.description or "") + f"（增量摄入 {len(delta_doc_ids)} 个新文档）",
        status="running",
    )
    runs.append(run)
    _save_runs(project_id, runs)

    import threading
    import asyncio

    def _run_in_thread():
        asyncio.run(_execute_incremental_async(project_id, run.id, delta_doc_ids))

    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()
    return run


async def _execute_incremental_async(project_id: str, run_id: str, only_doc_ids: List[str]):
    """增量抽取执行：仅处理 delta 文档，完成后记录已处理文档。"""
    print(f"[Incremental] 开始增量抽取: project={project_id}, run={run_id}, docs={only_doc_ids}")
    try:
        from services.extraction.graph import run_extraction_pipeline_sync
        from services.planning import compile_default_plan
        plan = compile_default_plan(project_id)
        await run_extraction_pipeline_sync(
            project_id=project_id,
            run_id=run_id,
            progress_callback=lambda **kw: _update_run(project_id, run_id, **kw),
            only_doc_ids=only_doc_ids,
            plan=plan,
        )
        # 记录已处理文档
        processed = _load_processed_docs(project_id)
        processed.extend(only_doc_ids)
        _save_processed_docs(project_id, processed)
        _update_run(
            project_id, run_id,
            status="completed",
            progress=100.0,
            current_step="增量抽取完成",
            completed_at=datetime.now().isoformat(),
        )
        print(f"[Incremental] 增量抽取完成: run={run_id}")
    except Exception as e:
        print(f"[Incremental] 增量抽取失败: {e}")
        traceback.print_exc()
        _update_run(
            project_id, run_id,
            status="failed",
            error_message=str(e),
            completed_at=datetime.now().isoformat(),
        )


@router.get("/{project_id}/runs", response_model=List[Run])
async def list_runs(project_id: str):
    """获取运行列表"""
    return _load_runs(project_id)


@router.get("/{project_id}/runs/{run_id}", response_model=Run)
async def get_run(project_id: str, run_id: str):
    """获取运行状态"""
    runs = _load_runs(project_id)
    for r in runs:
        if r.id == run_id:
            return r
    raise HTTPException(status_code=404, detail="任务不存在")


@router.post("/{project_id}/runs/{run_id}/resume", response_model=Run)
async def resume_run(project_id: str, run_id: str):
    """继续抽取出错或中断的任务"""
    runs = _load_runs(project_id)
    run = None
    for r in runs:
        if r.id == run_id:
            run = r
            break
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 重新标记状态
    run.status = "running"
    run.error_message = None
    run.updated_at = datetime.now().isoformat()
    _save_runs(project_id, runs)

    # 传递上一次的统计数据，让 pipeline 知道从哪里继续
    from services.extraction.graph import run_extraction_pipeline_sync
    import threading
    import asyncio
    
    from services.planning import compile_default_plan
    resume_plan = compile_default_plan(project_id)

    def _run_resume_in_thread():
        asyncio.run(run_extraction_pipeline_sync(
            project_id=project_id,
            run_id=run_id,
            progress_callback=lambda **kw: _update_run(project_id, run_id, **kw),
            initial_stats=run.stats,
            skip_chunks=run.stats.get("processed_chunks", 0),
            plan=resume_plan,
        ))

    t = threading.Thread(
        target=_run_resume_in_thread,
        daemon=True,
    )
    t.start()
    return run


@router.post("/{project_id}/runs/{run_id}/restart", response_model=Run)
async def restart_run(project_id: str, run_id: str):
    """完全重新开始抽取（清空状态和草稿图谱）"""
    runs = _load_runs(project_id)
    run = None
    for r in runs:
        if r.id == run_id:
            run = r
            break
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 清除当前的 draft graph
    from services.graph_store import load_draft_graph, save_draft_graph
    # 直接清空当前 draft_graph
    save_draft_graph(project_id, load_draft_graph(project_id).__class__(nodes=[], edges=[]))

    # 完全重置任务状态
    run.status = "running"
    run.progress = 0.0
    run.current_step = "重新启动中..."
    run.error_message = None
    run.updated_at = datetime.now().isoformat()
    # 重置 stats
    run.stats = {
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
    }
    _save_runs(project_id, runs)

    from services.extraction.graph import run_extraction_pipeline_sync
    import threading
    import asyncio
    
    from services.planning import compile_default_plan
    restart_plan = compile_default_plan(project_id)

    def _run_restart_in_thread():
        asyncio.run(run_extraction_pipeline_sync(
            project_id=project_id,
            run_id=run_id,
            progress_callback=lambda **kw: _update_run(project_id, run_id, **kw),
            initial_stats=run.stats,
            skip_chunks=0,
            plan=restart_plan,
        ))

    t = threading.Thread(
        target=_run_restart_in_thread,
        daemon=True,
    )
    t.start()
    return run


@router.get("/{project_id}/runs/{run_id}/logs")
async def get_run_logs(project_id: str, run_id: str, limit: int = 100):
    """获取指定任务的最新日志（最多 limit 行）"""
    log_file = get_project_dir(project_id) / "logs" / f"{run_id}.log"
    if not log_file.exists():
        return {"logs": []}
        
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Split by newline but don't discard empty/incomplete ends
            lines = content.split('\n')
            if lines and lines[-1] == "":
                lines.pop()
            return {"logs": lines[-limit:]}
    except Exception as e:
        return {"logs": [f"[系统] 读取日志失败: {e}"]}
