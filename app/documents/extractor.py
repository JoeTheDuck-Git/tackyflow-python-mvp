from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader


MAX_EXTRACTED_CHARACTERS = 50_000
MAX_UNCOMPRESSED_ARCHIVE_BYTES = 50 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".txt", ".md", ".srt", ".vtt", ".csv", ".pdf", ".docx", ".xlsx", ".pptx"}


class DocumentExtractionError(ValueError):
    pass


def extract_document_text(filename: str, data: bytes) -> dict[str, object]:
    safe_name = Path(filename).name
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        if extension in {".doc", ".xls", ".ppt"}:
            raise DocumentExtractionError("不支援舊版 Office 二進位格式，請先另存為 DOCX、XLSX 或 PPTX。")
        raise DocumentExtractionError("不支援此檔案格式。")
    if not data:
        raise DocumentExtractionError("檔案內容為空。")

    warnings: list[str] = []
    if extension in {".txt", ".md", ".srt", ".vtt", ".csv"}:
        text = _decode_text(data)
        unit_label = "文字檔"
        unit_count = max(1, text.count("\n") + 1)
    elif extension == ".pdf":
        text, unit_count, pdf_warnings = _extract_pdf(data)
        warnings.extend(pdf_warnings)
        unit_label = "頁"
    elif extension == ".docx":
        text, unit_count = _extract_docx(data)
        unit_label = "段落"
    elif extension == ".xlsx":
        text, unit_count = _extract_xlsx(data)
        unit_label = "工作表"
    else:
        text, unit_count = _extract_pptx(data)
        unit_label = "投影片"

    text = _normalize_text(text)
    if not text:
        raise DocumentExtractionError(
            "未能擷取到可讀文字；若是掃描型 PDF，請先執行 OCR 後再上傳。"
        )
    original_char_count = len(text)
    if original_char_count > MAX_EXTRACTED_CHARACTERS:
        text = text[:MAX_EXTRACTED_CHARACTERS].rstrip()
        warnings.append(f"文字超過 {MAX_EXTRACTED_CHARACTERS:,} 字，僅保留前段供 AI 參考。")
    return {
        "name": safe_name,
        "file_type": extension.removeprefix(".").upper(),
        "text": text,
        "character_count": len(text),
        "original_character_count": original_char_count,
        "unit_label": unit_label,
        "unit_count": unit_count,
        "warnings": warnings,
    }


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> tuple[str, int, list[str]]:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise DocumentExtractionError("PDF 已加密，請先解除密碼再上傳。") from exc
        pages: list[str] = []
        empty_pages = 0
        for index, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                empty_pages += 1
            pages.append(f"【第 {index} 頁】\n{page_text}" if page_text else "")
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError("PDF 無法解析，請確認檔案未損毀。") from exc
    warnings = []
    if empty_pages:
        warnings.append(f"有 {empty_pages} 頁未擷取到文字，可能包含掃描圖片或純圖表。")
    return "\n\n".join(part for part in pages if part), len(reader.pages), warnings


def _open_office_archive(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocumentExtractionError("Office 檔案無法解析，請確認檔案未損毀。") from exc
    if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_ARCHIVE_BYTES:
        archive.close()
        raise DocumentExtractionError("解壓後的文件過大，請移除大型圖片或拆分檔案。")
    return archive


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(xml: bytes, *, paragraph_tags: set[str]) -> str:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentExtractionError("文件內部 XML 無法解析。") from exc
    output: list[str] = []

    def walk(element: ElementTree.Element) -> None:
        name = _local_name(element.tag)
        if name in {"t", "v"} and element.text:
            output.append(element.text)
        elif name == "tab":
            output.append("\t")
        for child in element:
            walk(child)
        if name in paragraph_tags:
            output.append("\n")

    walk(root)
    return "".join(output)


def _extract_docx(data: bytes) -> tuple[str, int]:
    with _open_office_archive(data) as archive:
        names = ["word/document.xml"] + sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"word/(header|footer)\d+\.xml", name)
        )
        if "word/document.xml" not in archive.namelist():
            raise DocumentExtractionError("DOCX 缺少主要文件內容。")
        parts = [_xml_text(archive.read(name), paragraph_tags={"p", "tr"}) for name in names]
    text = "\n".join(parts)
    return text, len([line for line in text.splitlines() if line.strip()])


def _extract_xlsx(data: bytes) -> tuple[str, int]:
    with _open_office_archive(data) as archive:
        names = archive.namelist()
        sheet_names = sorted(
            (name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        if not sheet_names:
            raise DocumentExtractionError("XLSX 未包含可讀取的工作表。")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter():
                if _local_name(item.tag) == "si":
                    shared.append("".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t"))
        sheets: list[str] = []
        for sheet_index, name in enumerate(sheet_names, start=1):
            root = ElementTree.fromstring(archive.read(name))
            rows: list[str] = []
            for row in (node for node in root.iter() if _local_name(node.tag) == "row"):
                cells: list[str] = []
                for cell in (node for node in row if _local_name(node.tag) == "c"):
                    cell_type = cell.attrib.get("t")
                    value_node = next((node for node in cell.iter() if _local_name(node.tag) == "v"), None)
                    if cell_type == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iter() if _local_name(node.tag) == "t")
                    elif value_node is not None and value_node.text is not None:
                        value = value_node.text
                        if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                    else:
                        value = ""
                    cells.append(value)
                if any(cell.strip() for cell in cells):
                    rows.append("\t".join(cells))
            sheets.append(f"【工作表 {sheet_index}】\n" + "\n".join(rows))
    return "\n\n".join(sheets), len(sheet_names)


def _extract_pptx(data: bytes) -> tuple[str, int]:
    with _open_office_archive(data) as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        if not slide_names:
            raise DocumentExtractionError("PPTX 未包含可讀取的投影片。")
        slides = [
            f"【投影片 {index}】\n{_xml_text(archive.read(name), paragraph_tags={'p'}).strip()}"
            for index, name in enumerate(slide_names, start=1)
        ]
    return "\n\n".join(slides), len(slide_names)


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line:
            normalized.append(line)
            blank = False
        elif normalized and not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()
