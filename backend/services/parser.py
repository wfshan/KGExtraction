"""
文档解析器
支持 PDF、TXT、Markdown、DOCX（含表格）、CSV、XLSX 格式的文本提取。
结构化文件（CSV/XLSX）统一输出为「表头行 + 每行 col | col | col」，
首个非空行即表头，供输入理解阶段作为候选 Schema 信号。
"""
import csv
from pathlib import Path


def parse_document(file_path: Path, file_type: str) -> str:
    """
    解析文档，提取纯文本内容

    Args:
        file_path: 文件路径
        file_type: 文件类型 (pdf/txt/md/docx/csv/xlsx)

    Returns:
        提取的纯文本
    """
    parsers = {
        "pdf": _parse_pdf,
        "txt": _parse_txt,
        "md": _parse_markdown,
        "docx": _parse_docx,
        "csv": _parse_csv,
        "xlsx": _parse_xlsx,
    }

    parser = parsers.get(file_type)
    if not parser:
        raise ValueError(f"不支持的文件类型: {file_type}")

    text = parser(file_path)

    # 基础清洗
    text = _clean_text(text)
    return text


def _parse_pdf(file_path: Path) -> str:
    """解析 PDF 文件"""
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    texts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            texts.append(page_text)
    return "\n\n".join(texts)


def _parse_txt(file_path: Path) -> str:
    """解析 TXT 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_markdown(file_path: Path) -> str:
    """解析 Markdown 文件，剥离标记保留纯文本"""
    from markdown_it import MarkdownIt

    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    md = MarkdownIt()
    tokens = md.parse(md_text)

    texts = []
    for token in tokens:
        if token.children:
            for child in token.children:
                if child.type == "text" or child.type == "code_inline":
                    texts.append(child.content)
        elif token.content:
            texts.append(token.content)

    return "\n".join(texts)


def _parse_docx(file_path: Path) -> str:
    """解析 DOCX 文件"""
    from docx import Document

    doc = Document(str(file_path))
    texts = []
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            texts.append(paragraph.text)

    # 表格内容
    for table in doc.tables:
        for row in table.rows:
            row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_texts:
                texts.append(" | ".join(row_texts))

    return "\n\n".join(texts)


def _parse_csv(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(file_path, "r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                rows = []
                for row in reader:
                    cells = [cell.strip() for cell in row]
                    if any(cells):
                        rows.append(" | ".join(cells))
                return "\n".join(rows)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 文件编码不受支持，请使用 UTF-8 或 GB18030 编码")


def _parse_xlsx(file_path: Path) -> str:
    """解析 Excel（.xlsx）。多工作表分别输出，每表首行为表头。"""
    from openpyxl import load_workbook

    wb = load_workbook(str(file_path), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [("" if v is None else str(v)).strip() for v in row]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            # 多表时标注表名，便于后续识别表间关系
            header = f"# 工作表: {ws.title}" if len(wb.worksheets) > 1 else ""
            sheets.append((header + "\n" if header else "") + "\n".join(rows))
    wb.close()
    return "\n\n".join(sheets)


def _clean_text(text: str) -> str:
    """基础文本清洗"""
    import re

    # 去除多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去除行首尾空白
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    # 去除首尾空白
    text = text.strip()

    return text
