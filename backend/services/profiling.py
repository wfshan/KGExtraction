"""输入理解：文件预处理、分层抽样、Map-Reduce 领域概括（对应设计文档第四章）。

三件事，供 Schema 建议与规划器复用：
1. build_file_profiles —— 每个文件的结构化元信息（文件名/目录/表头/规模）。
2. stratified_sample   —— 两级分层抽样：文件级保底 + 大小加权，文件内首段+均匀。
   避免"片段级拉平随机"在文件规模不均时被大文件主导、漏掉小文件领域。
3. map_reduce_profile  —— 每文件 Map 出画像，再全局 Reduce 出领域概括；文件多也不稀释。

产物缓存于 project_dir/schema_profile.json（结构化）与 schema_profile.md（可读，向后兼容）。
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

from config import get_project_dir
from services.llm_gateway import llm_gateway, COMPLEXITY_NORMAL, COMPLEXITY_COMPLEX

logger = logging.getLogger(__name__)

STRUCTURED_EXTS = {"csv", "xlsx", "xls"}
_HEADING_RE = re.compile(r"^\s*(#{1,6}\s+\S|第[一二三四五六七八九十百]+[章节部分篇]|[一二三四五六七八九十]+、|\d+(\.\d+)*[\s、.]+\S)")


# ---------------------------------------------------------------- 元信息

def _load_documents_meta(project_id: str) -> Dict[str, Dict]:
    """doc_id → {filename, file_type, file_size}。"""
    docs_file = get_project_dir(project_id) / "documents.json"
    meta: Dict[str, Dict] = {}
    if not docs_file.exists():
        return meta
    try:
        with open(docs_file, "r", encoding="utf-8") as f:
            for d in json.load(f):
                meta[d["id"]] = {
                    "filename": d.get("original_filename", d.get("filename", d["id"])),
                    "file_type": (d.get("file_type", "") or "").lower(),
                    "file_size": d.get("file_size", 0),
                }
    except Exception as e:
        logger.warning(f"[profiling] 读取 documents.json 失败: {e}")
    return meta


def _load_chunks_by_doc(project_id: str) -> Dict[str, List[str]]:
    """doc_id → [chunk 文本]（按 index 有序）。"""
    chunks_dir = get_project_dir(project_id) / "chunks"
    by_doc: Dict[str, List[Tuple[int, str]]] = {}
    if chunks_dir.exists():
        for cf in chunks_dir.glob("*_chunks.json"):
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    for c in json.load(f):
                        doc_id = c.get("doc_id", cf.name.replace("_chunks.json", ""))
                        by_doc.setdefault(doc_id, []).append((c.get("index", 0), c.get("text", "")))
            except Exception:
                continue
    return {d: [t for _, t in sorted(lst, key=lambda x: x[0])] for d, lst in by_doc.items()}


def _extract_outline(raw_text: str, max_items: int = 15) -> List[str]:
    """从原文提取目录/标题层级（启发式）。"""
    outline = []
    for line in raw_text.splitlines():
        line = line.strip()
        if 2 <= len(line) <= 40 and _HEADING_RE.match(line):
            outline.append(line[:40])
            if len(outline) >= max_items:
                break
    return outline


def build_file_profiles(project_id: str) -> List[Dict]:
    """每个文件的结构化元信息（不调用 LLM，纯确定性）。"""
    meta = _load_documents_meta(project_id)
    by_doc = _load_chunks_by_doc(project_id)
    chunks_dir = get_project_dir(project_id) / "chunks"

    profiles = []
    for doc_id, chunks in by_doc.items():
        m = meta.get(doc_id, {})
        ext = m.get("file_type", "")
        is_structured = ext in STRUCTURED_EXTS

        raw_text = ""
        raw_file = chunks_dir / f"{doc_id}_raw.txt"
        if raw_file.exists():
            try:
                raw_text = raw_file.read_text(encoding="utf-8")
            except Exception:
                raw_text = "\n".join(chunks[:3])
        else:
            raw_text = "\n".join(chunks[:3])

        # 结构化文件：首个非空且非工作表标记的行即表头（解析器输出为 "col | col | col"）
        headers: List[str] = []
        if is_structured:
            for line in raw_text.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                headers = [h.strip() for h in s.split("|") if h.strip()]
                break

        profiles.append({
            "doc_id": doc_id,
            "filename": m.get("filename", doc_id),
            "ext": ext,
            "is_structured": is_structured,
            "file_size": m.get("file_size", sum(len(t) for t in chunks)),
            "chunk_count": len(chunks),
            "outline": [] if is_structured else _extract_outline(raw_text),
            "headers": headers,
            "domain_hint": Path(m.get("filename", doc_id)).stem,
        })
    return profiles


# ---------------------------------------------------------------- 分层抽样

def _sample_within(chunks: List[str], k: int) -> List[str]:
    """文件内结构感知抽样：必含首段（常为标题/摘要），其余均匀铺开。"""
    if k <= 0 or not chunks:
        return []
    if len(chunks) <= k:
        return chunks
    idxs = [0]
    if k > 1:
        step = (len(chunks) - 1) / (k - 1)
        idxs += [round(i * step) for i in range(1, k)]
    seen, out = set(), []
    for i in idxs:
        i = min(i, len(chunks) - 1)
        if i not in seen:
            seen.add(i)
            out.append(chunks[i])
    return out


def stratified_sample(project_id: str, budget: int = 18) -> Tuple[Dict[str, List[str]], List[str], int]:
    """两级分层抽样。返回 (按 doc_id 分组的样本, 拉平样本, 总片段数)。

    - 文件级：每文件保底配额（小文件不被淹没），剩余按片段数加权分配。
    - 文件多于预算时：跨文件均匀挑选代表文件，各取首段，保证领域覆盖面。
    """
    by_doc = _load_chunks_by_doc(project_id)
    docs = [(d, c) for d, c in by_doc.items() if c]
    total = sum(len(c) for _, c in docs)
    if not docs:
        return {}, [], 0

    n = len(docs)
    grouped: Dict[str, List[str]] = {}

    if n >= budget:
        # 文件数超预算：均匀挑 budget 个代表文件，各取首段
        step = n / budget
        for i in range(budget):
            doc_id, chunks = docs[int(i * step)]
            grouped[doc_id] = _sample_within(chunks, 1)
    else:
        floor = 2 if budget >= 2 * n else 1
        remaining = budget - floor * n
        total_chunks = sum(len(c) for _, c in docs) or 1
        for doc_id, chunks in docs:
            extra = round(remaining * len(chunks) / total_chunks) if remaining > 0 else 0
            grouped[doc_id] = _sample_within(chunks, floor + extra)

    flat = [t for chunks in grouped.values() for t in chunks]
    return grouped, flat, total


# ---------------------------------------------------------------- Map-Reduce

_MAP_PROMPT = """你是领域分析专家。请阅读单个文件的元信息与代表性片段，输出该文件的领域画像。

## 文件元信息
文件名：{filename}
类型：{ext}（{structured}）
目录/标题：{outline}
表头（若为结构化文件）：{headers}

## 代表性片段
{samples}

## 输出（严格 JSON）
{{
  "domain": "该文件所属领域/主题",
  "doc_type": "文档类型（报告/合同/明细表/手册/论文等）",
  "key_concepts": [
    {{"name": "核心概念", "abstractness": "surface|normalized|inductive"}}
  ],
  "note": "其他重要观察（一句话）"
}}

抽象度判断：surface=文本中可直接定位的实体（人名/账号/文件名）；normalized=需标准化的值（日期/金额）；inductive=需从案例归纳的抽象知识（规则/模式/结论）。结构化文件的列多为 surface/normalized。"""

_REDUCE_PROMPT = """你是知识工程师。下面是一个项目中各文件的领域画像。请汇总为该项目的**全局领域概括**，用于指导本体（Schema）设计。

## 各文件画像
{file_analyses}

## 输出要求（Markdown，简洁）
1. **整体领域**：这批材料整体属于什么领域、要解决什么问题。
2. **子领域划分**：若混合多个领域/文档类型，分别列出。
3. **核心概念**：跨文件共性的关键实体与抽象概念，标注其抽象度倾向（表面/标准化/归纳）。
4. **异质提示**：是否存在领域跨度大、结构化与非结构化混杂等需要注意的情况。"""


async def _map_one_file(fp: Dict, samples: List[str]) -> Dict:
    """对单个文件产出领域画像（LLM）。失败时回退为基于元信息的粗画像。"""
    try:
        result = await llm_gateway.chat_json(
            messages=[
                {"role": "system", "content": "你是严谨的领域分析专家，只返回 JSON。"},
                {"role": "user", "content": _MAP_PROMPT.format(
                    filename=fp["filename"],
                    ext=fp["ext"],
                    structured="结构化" if fp["is_structured"] else "非结构化",
                    outline="、".join(fp["outline"][:10]) or "（无）",
                    headers="、".join(fp["headers"]) or "（无）",
                    samples="\n---\n".join(s[:500] for s in samples[:6]) or "（无样本）",
                )},
            ],
            complexity=COMPLEXITY_NORMAL,
        )
        result["filename"] = fp["filename"]
        return result
    except Exception as e:
        logger.warning(f"[profiling] 文件 Map 失败 {fp['filename']}: {e}")
        return {
            "filename": fp["filename"],
            "domain": fp["domain_hint"],
            "doc_type": "结构化数据" if fp["is_structured"] else "文档",
            "key_concepts": [{"name": h, "abstractness": "surface"} for h in fp["headers"][:8]],
            "note": "（LLM 分析不可用，基于元信息推断）",
        }


async def map_reduce_profile(project_id: str, budget: int = 18, force: bool = False) -> Tuple[str, List[Dict], Dict[str, List[str]]]:
    """Map-Reduce 领域概括。返回 (全局概括 Markdown, 各文件画像, 结构化文件表头)。"""
    profile_json = get_project_dir(project_id) / "schema_profile.json"
    profile_md = get_project_dir(project_id) / "schema_profile.md"

    if not force and profile_json.exists():
        try:
            cached = json.loads(profile_json.read_text(encoding="utf-8"))
            return cached.get("global_md", ""), cached.get("file_analyses", []), cached.get("structured_headers", {})
        except Exception:
            pass

    file_profiles = build_file_profiles(project_id)
    grouped, _flat, total = stratified_sample(project_id, budget)
    if total == 0:
        return "", [], {}

    fp_by_doc = {fp["doc_id"]: fp for fp in file_profiles}
    # 仅对被抽到样本的文件做 Map（当文件多于预算时天然限流，成本受 budget 约束）
    map_tasks = [
        _map_one_file(fp_by_doc[doc_id], samples)
        for doc_id, samples in grouped.items() if doc_id in fp_by_doc and samples
    ]
    file_analyses = await asyncio.gather(*map_tasks) if map_tasks else []

    # Reduce：输入是画像而非原文，省 token、不截断
    global_md = ""
    if file_analyses:
        try:
            resp = await llm_gateway.chat(
                messages=[
                    {"role": "system", "content": "你是资深知识工程师，输出简洁的 Markdown。"},
                    {"role": "user", "content": _REDUCE_PROMPT.format(
                        file_analyses=json.dumps(file_analyses, ensure_ascii=False, indent=2),
                    )},
                ],
                complexity=COMPLEXITY_COMPLEX,
                stream_log=True,
            )
            global_md = resp["content"]
        except Exception as e:
            logger.warning(f"[profiling] Reduce 失败: {e}")
            global_md = _fallback_global(file_analyses)

    structured_headers = {
        fp["filename"]: fp["headers"]
        for fp in file_profiles if fp["is_structured"] and fp["headers"]
    }

    try:
        profile_json.write_text(json.dumps({
            "global_md": global_md, "file_analyses": file_analyses, "structured_headers": structured_headers,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        profile_md.write_text(global_md, encoding="utf-8")
    except Exception as e:
        logger.warning(f"[profiling] 写入缓存失败: {e}")

    return global_md, file_analyses, structured_headers


def _fallback_global(file_analyses: List[Dict]) -> str:
    domains = sorted({a.get("domain", "") for a in file_analyses if a.get("domain")})
    concepts = sorted({c.get("name", "") for a in file_analyses for c in a.get("key_concepts", []) if c.get("name")})
    return (
        "## 全局领域概括（自动汇总）\n\n"
        f"- 涉及领域：{', '.join(domains) or '未知'}\n"
        f"- 核心概念：{', '.join(concepts[:30]) or '未知'}\n"
        f"- 文件数：{len(file_analyses)}"
    )
