from io import BytesIO
import zipfile

import pytest

from app.documents import DocumentExtractionError, extract_document_text


def office_archive(files: dict[str, str]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_extracts_docx_text_for_human_review() -> None:
    payload = office_archive({
        "word/document.xml": """<w:document xmlns:w="urn:w"><w:body><w:p><w:r><w:t>品牌語氣：專業但自然</w:t></w:r></w:p><w:p><w:r><w:t>禁用誇大承諾</w:t></w:r></w:p></w:body></w:document>""",
    })

    result = extract_document_text("brand.docx", payload)

    assert result["file_type"] == "DOCX"
    assert "品牌語氣：專業但自然" in result["text"]
    assert "禁用誇大承諾" in result["text"]
    assert result["unit_label"] == "段落"


def test_extracts_xlsx_cells_and_pptx_slides() -> None:
    workbook = office_archive({
        "xl/worksheets/sheet1.xml": """<worksheet xmlns="urn:x"><sheetData><row><c t="inlineStr"><is><t>產品名稱</t></is></c><c t="inlineStr"><is><t>Osmo 360</t></is></c></row></sheetData></worksheet>""",
    })
    deck = office_archive({
        "ppt/slides/slide1.xml": """<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:p><a:r><a:t>品牌核心價值</a:t></a:r></a:p></p:cSld></p:sld>""",
        "ppt/slides/slide2.xml": """<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:p><a:r><a:t>語氣規範</a:t></a:r></a:p></p:cSld></p:sld>""",
    })

    sheet_result = extract_document_text("facts.xlsx", workbook)
    slide_result = extract_document_text("brief.pptx", deck)

    assert "產品名稱 Osmo 360" in sheet_result["text"]
    assert sheet_result["unit_count"] == 1
    assert "【投影片 1】" in slide_result["text"]
    assert "品牌核心價值" in slide_result["text"]
    assert slide_result["unit_count"] == 2


def test_rejects_legacy_office_and_empty_extraction() -> None:
    with pytest.raises(DocumentExtractionError, match="DOCX、XLSX 或 PPTX"):
        extract_document_text("legacy.doc", b"binary")
    with pytest.raises(DocumentExtractionError, match="未能擷取"):
        extract_document_text("empty.txt", b"   \n")
