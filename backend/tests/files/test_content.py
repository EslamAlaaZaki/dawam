"""Work caps of text extraction: huge or blank documents end quickly."""

from __future__ import annotations

import io

from docx import Document
from openpyxl import Workbook

from dawam.modules.files.internal import content


def xlsx(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def docx(paragraphs: list[str]) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_blank_rows_count_toward_the_row_cap(monkeypatch):
    monkeypatch.setattr(content, "XLSX_MAX_ROWS", 10)
    data = xlsx([[None, None]] * 50 + [["findme"]])

    text, status = content.extract_text(content.XLSX, data)

    assert status == "extracted"  # only the sheet heading
    assert "findme" not in text


def test_rows_within_the_cap_are_read(monkeypatch):
    monkeypatch.setattr(content, "XLSX_MAX_ROWS", 10)

    text, status = content.extract_text(content.XLSX, xlsx([["a", "b"], [None], ["c"]]))

    assert status == "extracted"
    assert "a\tb" in text
    assert "c" in text


def test_the_cell_cap_stops_a_wide_sheet(monkeypatch):
    monkeypatch.setattr(content, "XLSX_MAX_CELLS", 10)

    text, _ = content.extract_text(content.XLSX, xlsx([["x"] * 6, ["y"] * 6, ["z"] * 6]))

    assert "z" not in text


def test_text_stops_being_built_at_the_character_cap(monkeypatch):
    monkeypatch.setattr(content, "EXTRACTED_TEXT_MAX_CHARS", 50)
    data = docx([f"paragraph {n:04d} " + "x" * 20 for n in range(1000)])

    text, status = content.extract_text(content.DOCX, data)

    assert status == "extracted"
    assert len(text) <= 50
    assert "paragraph 0999" not in text


def test_the_paragraph_cap_limits_a_docx(monkeypatch):
    monkeypatch.setattr(content, "DOCX_MAX_PARAGRAPHS", 3)

    text, _ = content.extract_text(content.DOCX, docx([f"para{n}" for n in range(10)]))

    assert text == "para0\npara1\npara2"
