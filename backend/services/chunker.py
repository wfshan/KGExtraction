"""
文本分片模块
将文档文本按段落/语义切分为 chunks
"""
import uuid
from typing import List, Dict


def chunk_text(
    text: str,
    doc_id: str,
    chunk_method: str = "fixed_length",
    chunk_size: int = 500,
    chunk_overlap: int = 100,
    hierarchical_level: int = 1,
    file_type: str = "md"
) -> List[Dict]:
    """
    将文本按指定策略切分为 chunks

    Args:
        text: 原始文本
        doc_id: 文档 ID
        chunk_method: 切分策略 "fixed_length" | "recursive_character" | "paragraph" | "hierarchical"
        chunk_size: 每片最大字符数 (用于 fixed_length 和 recursive_character)
        chunk_overlap: 片段间重叠字符数
        hierarchical_level: 目标切分深度 (用于 hierarchical)
        file_type: 文件类型 (用于 hierarchical 辅助识别)

    Returns:
        分片列表，每项包含 id, doc_id, text, index, start_char, end_char
    """
    if not text.strip():
        return []

    if chunk_method == "paragraph":
        return _chunk_by_paragraph(text, doc_id)
    elif chunk_method == "recursive_character":
        return _chunk_recursive_character(text, doc_id, chunk_size, chunk_overlap)
    elif chunk_method == "hierarchical":
        return _chunk_hierarchical(text, doc_id, hierarchical_level, file_type)
    else:
        # 默认回归 fixed_length 滑动窗口模式
        return _chunk_fixed_length(text, doc_id, chunk_size, chunk_overlap)


def _chunk_fixed_length(text: str, doc_id: str, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    """固定长度 + 滑动窗口的通用切分策略 (原始方案)"""
    paragraphs = text.split("\n\n")
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 合并过短的段落，拆分过长的段落
    chunks = []
    current_chunk = ""
    current_start = 0
    char_pos = 0

    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
                current_start = char_pos
        else:
            # 当前 chunk 已满
            if current_chunk:
                chunks.append({
                    "id": str(uuid.uuid4()),
                    "doc_id": doc_id,
                    "text": current_chunk,
                    "index": len(chunks),
                    "start_char": current_start,
                    "end_char": current_start + len(current_chunk),
                })

            # 如果单个段落超过 chunk_size，滑动窗口切分
            if len(para) > chunk_size:
                sub_chunks = _sliding_window(para, chunk_size, chunk_overlap)
                for sc in sub_chunks:
                    chunks.append({
                        "id": str(uuid.uuid4()),
                        "doc_id": doc_id,
                        "text": sc,
                        "index": len(chunks),
                        "start_char": char_pos,
                        "end_char": char_pos + len(sc),
                    })
                current_chunk = ""
            else:
                current_chunk = para
                current_start = char_pos

        char_pos += len(para) + 2  # +2 for \n\n

    # 最后一个 chunk
    if current_chunk:
        chunks.append({
            "id": str(uuid.uuid4()),
            "doc_id": doc_id,
            "text": current_chunk,
            "index": len(chunks),
            "start_char": current_start,
            "end_char": current_start + len(current_chunk),
        })

    return chunks


def _sliding_window(text: str, size: int, overlap: int) -> List[str]:
    """滑动窗口切分长文本"""
    windows = []
    start = 0
    while start < len(text):
        end = start + size
        windows.append(text[start:end])
        start += size - overlap
        if start >= len(text):
            break
    return windows


def _chunk_by_paragraph(text: str, doc_id: str) -> List[Dict]:
    """按段落 (`\\n\\n`) 严格切分，无视长度限制。

    起始偏移用顺序累积精确计算（split 的每段位置是确定的），不再用 text.find 兜底——
    重复段落会让 find 命中错误位置，导致逐字证据校验的溯源偏移。
    """
    paragraphs = text.split("\n\n")
    chunks = []

    offset = 0  # 当前段落在原文中的起始位置（顺序消费）
    for para in paragraphs:
        cleaned = para.strip()
        if cleaned:
            # strip 只去两端，段内内容连续，起点 = 段落起点 + 前导空白长度
            start_pos = offset + (len(para) - len(para.lstrip()))
            chunks.append({
                "id": str(uuid.uuid4()),
                "doc_id": doc_id,
                "text": cleaned,
                "index": len(chunks),
                "start_char": start_pos,
                "end_char": start_pos + len(cleaned),
            })
        offset += len(para) + 2  # 补回 split 吃掉的 "\n\n"

    return chunks


def _chunk_recursive_character(text: str, doc_id: str, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    """
    递归字符切分（类似 LangChain RecursiveCharacterTextSplitter）
    尝试以 ['\\n\\n', '\\n', '。', '.', '!', '?', ' ', ''] 作为分隔符递进切分
    """
    import uuid
    separators = ["\n\n", "\n", "。", ".", "！", "!", "？", "?", " ", ""]
    
    def _split_text(txt: str, sep_index: int) -> List[str]:
        # 如果当前片段已经满足大小，或者没有分隔符可用了，就直接整块返回（或硬切）
        if len(txt) <= chunk_size:
            return [txt]
        if sep_index >= len(separators):
            # 走到最后连单字符都没法分了，只能按窗口硬切了
            return _sliding_window(txt, chunk_size, chunk_overlap)
            
        separator = separators[sep_index]
        if separator == "":
            splits = list(txt)
        else:
            splits = txt.split(separator)
            
        # 重新组合 splits
        good_splits = []
        current_doc = ""
        
        for i, s in enumerate(splits):
            if current_doc:
                # 尝试加上分隔符
                attempt = current_doc + separator + s if separator else current_doc + s
                if len(attempt) <= chunk_size:
                    current_doc = attempt
                else:
                    # 如果装不下，先把前面的存入
                    if len(current_doc) > chunk_size:
                        # 极端情况：组装好的哪怕只有一块也超了，那就递归降低层级
                        if separator:
                            # 尝试继续拆分
                             good_splits.extend(_split_text(current_doc, sep_index + 1))
                        else:
                             # 已经没分隔符了，硬切
                             good_splits.extend(_sliding_window(current_doc, chunk_size, chunk_overlap))
                    else:
                        good_splits.append(current_doc)
                    current_doc = s
            else:
                current_doc = s
                
        if current_doc:
            if len(current_doc) > chunk_size:
                if separator:
                    good_splits.extend(_split_text(current_doc, sep_index + 1))
                else:
                    good_splits.extend(_sliding_window(current_doc, chunk_size, chunk_overlap))
            else:
                good_splits.append(current_doc)
                
        return good_splits

    text_list = _split_text(text, 0)
    
    # 组装返回 Dict
    chunks = []
    char_pos = 0
    for txt in text_list:
        start_pos = text.find(txt, char_pos)
        if start_pos == -1:
            start_pos = char_pos
            
        chunks.append({
            "id": str(uuid.uuid4()),
            "doc_id": doc_id,
            "text": txt,
            "index": len(chunks),
            "start_char": start_pos,
            "end_char": start_pos + len(txt),
        })
        char_pos = start_pos + len(txt)
        
    return chunks


def _chunk_hierarchical(text: str, doc_id: str, target_level: int, file_type: str) -> List[Dict]:
    """
    根据层级结构拆分，并带上上级标题作为上下文。
    支持多种样式的标题识别。
    """
    import re

    # 1. 定义标题正则库
    patterns = [
        (r"^(#{1,6})\s+(.*)$", "md"),                       # Markdown # Header
        (r"^第([一二三四五六七八九十百]+)[章节规回].*$", "chapter"), # 第一章 ...
        (r"^(\d+(\.\d+)*)\s+.*$", "numbered"),              # 1.1 / 1.1.1 ...
        (r"^([一二三四五六七八九十]+)、.*$", "chinese_num"),    # 一、 ...
    ]

    # 2. 将文本按行分割并识别标题节点
    lines = text.split("\n")
    nodes = [] # 每个元素: {level, title, lines, full_header}
    
    current_parents = [None] * 7 # 记录当前各级的标题内容

    def get_level(line):
        # 尝试匹配各种模式
        # MD 模式
        md_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if md_match:
            return len(md_match.group(1)), md_match.group(0)
        
        # 数字 模式 (1.1.1)
        num_match = re.match(r"^(\d+(\.\d+)*)\s+(.*)$", line)
        if num_match:
            level = num_match.group(1).count(".") + 1
            return level, line
            
        # 中文序号模式 (一、)
        cn_match = re.match(r"^([一二三四五六七八九十]+)、(.*)$", line)
        if cn_match:
            return 2, line # 粗略映射为二级

        # 章节模式 (第一章)
        ch_match = re.match(r"^第([一二三四五六七八九十百]+)[章节规回](.*)$", line)
        if ch_match:
            return 1, line # 粗略映射为一级
            
        return None, None

    # 第一遍扫描：构建扁平的节点列表（包含正文归属）
    current_node = {"level": 0, "title": "Root", "lines": [], "parents": []}
    nodes.append(current_node)

    for line in lines:
        if not line.strip():
            current_node["lines"].append(line)
            continue
            
        level, header = get_level(line)
        if level is not None:
            # 这是一个标题
            # 更新 parents 数组
            current_parents[level] = header
            for i in range(level + 1, 7):
                current_parents[i] = None
                
            # 获取当前标题的父级路径
            parents = [p for p in current_parents[1:level] if p]
            
            # 创建新节点
            current_node = {
                "level": level,
                "title": header,
                "lines": [line],
                "parents": parents
            }
            nodes.append(current_node)
        else:
            # 这是一个普通正文行
            current_node["lines"].append(line)

    # 第二遍扫描：根据 target_level 合并
    # 如果 target_level 是 2，则合并所有以 level 2 标题开始的内容，直到下一个 level <= 2 的标题
    final_results = []
    
    # 我们需要合并逻辑：
    # 将 nodes 按照层级聚合为树，然后提取指定层级的子树。
    # 这里采用一种简化方案：找到所有 level=target_level 的起始索引，
    # 或者是 level > 0 且最接近 target_level 的。
    
    target_nodes = []
    for i, node in enumerate(nodes):
        if node["level"] == target_level:
            target_nodes.append(i)
        elif node["level"] > 0 and node["level"] < target_level and i > 0:
            # 如果中间夹杂了更高级别的标题，我们需要重置合并逻辑？
            # 实际上 target_nodes 已经定义了每个块的起点。
            pass

    # 如果没找到任何 target_level，尝试找更高层级的（降级处理）
    if not target_nodes:
        potential_levels = sorted(list(set(n["level"] for n in nodes if n["level"] > 0)))
        if potential_levels:
            # 找一个最接近的
            best_level = potential_levels[0]
            for i, node in enumerate(nodes):
                if node["level"] == best_level:
                    target_nodes.append(i)

    # 如果还是没找到标题（纯文本），就整体作为一个块
    if not target_nodes:
        full_text = "\n".join(["\n".join(n["lines"]) for n in nodes])
        if full_text.strip():
            final_results.append({
                "text": full_text,
                "parents": []
            })
    else:
        # 进行合并
        for i, start_idx in enumerate(target_nodes):
            end_idx = target_nodes[i+1] if i + 1 < len(target_nodes) else len(nodes)
            
            # 检查中间是否有更高级别的标题，如果有，则截断合并
            # 但通常层级切分是“包含子级”的，比如 H1 下面有 H2，如果切 H1，H2 就在里面。
            # 如果切 H2，那么遇到下一个 H1 或 H2 都会停止。
            for j in range(start_idx + 1, end_idx):
                if nodes[j]["level"] > 0 and nodes[j]["level"] < target_level:
                    end_idx = j
                    break
            
            # 组装文本
            chunk_lines = []
            # 补全上下文
            for p in nodes[start_idx]["parents"]:
                chunk_lines.append(p)
            
            # 添加本级及子级内容
            for j in range(start_idx, end_idx):
                chunk_lines.extend(nodes[j]["lines"])
                
            final_results.append({
                "text": "\n".join(chunk_lines).strip(),
                "parents": nodes[start_idx]["parents"]
            })

    # 3. 组装返回 Dict
    chunks = []
    char_pos = 0 # 粗略模拟
    for i, res in enumerate(final_results):
        if not res["text"].strip():
            continue
        chunks.append({
            "id": str(uuid.uuid4()),
            "doc_id": doc_id,
            "text": res["text"],
            "index": i,
            "start_char": char_pos,
            "end_char": char_pos + len(res["text"]),
            "metadata": {"parents": res["parents"]}
        })
        char_pos += len(res["text"])
        
    return chunks
