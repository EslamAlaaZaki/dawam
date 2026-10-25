"""Per-passage metadata (document type, language, page, sheet, tags) and the search filters
that use it. Driven through the HTTP API, with the scripted fake provider behind every LLM
provider (no real model is called)."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter

from tests.files.test_files import system, text_pdf, upload
from tests.files.test_search import policy, search, upload_ok  # noqa: F401

NEEDLE = "settlement ledger"


def upload_bytes(roles, system_id: str, name: str, data: bytes) -> None:
    response = upload(roles, system_id, name, data)
    assert response.status_code == 201, response.text


def two_page_pdf() -> bytes:
    writer = PdfWriter()
    for text in ("Settlement ledger page one", "Settlement ledger page two"):
        writer.add_page(PdfReader(io.BytesIO(text_pdf(text))).pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def two_sheet_xlsx() -> bytes:
    workbook = Workbook()
    workbook.active.title = "Codes"
    workbook.active.append(["settlement ledger code"])
    workbook.create_sheet("Rules").append(["settlement ledger rule"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def corpus(roles, policy):  # noqa: F811
    sid = system(roles)
    upload_ok(roles, sid, "notes.md", "# Notes\nThe settlement ledger is booked daily.")
    upload_ok(roles, sid, "ar.md", "# دفتر\nدفتر الأستاذ settlement ledger يقيد يوميا بالتفصيل.")
    upload_bytes(roles, sid, "spec.pdf", two_page_pdf())
    upload_bytes(roles, sid, "codes.xlsx", two_sheet_xlsx())
    return sid


def found(roles, **filters) -> set[tuple]:
    response = search(roles, NEEDLE, **filters)
    assert response.status_code == 200, response.text
    return {(p["document"], p["page"], p["sheet"]) for p in response.json()["items"]}


def names(roles, **filters) -> set[str]:
    return {name for name, _, _ in found(roles, **filters)}


def test_without_filters_every_matching_document_is_found(roles, corpus):
    assert names(roles) == {"notes.md", "ar.md", "spec.pdf", "codes.xlsx"}


def test_passages_carry_their_metadata(roles, corpus):
    items = search(roles, NEEDLE, document_type="pdf").json()["items"]
    assert items and all(p["document_type"] == "pdf" and p["language"] == "en" for p in items)


def test_document_type_narrows_results(roles, corpus):
    assert names(roles, document_type="xlsx") == {"codes.xlsx"}
    assert names(roles, document_type="PDF") == {"spec.pdf"}


def test_page_narrows_a_pdf_to_that_page(roles, corpus):
    assert found(roles, page=2) == {("spec.pdf", 2, None)}
    assert found(roles, page=1) == {("spec.pdf", 1, None)}
    assert found(roles, page=3) == set()


def test_sheet_narrows_a_workbook_to_that_sheet(roles, corpus):
    assert found(roles, sheet="Rules") == {("codes.xlsx", None, "Rules")}


def test_language_narrows_results(roles, corpus):
    assert names(roles, language="ar") == {"ar.md"}
    assert "ar.md" not in names(roles, language="en")


def test_tag_narrows_results_and_an_unknown_tag_finds_nothing(roles, corpus):
    assert names(roles, tag="nowhere") == set()


def test_filters_combine(roles, corpus):
    assert found(roles, document_type="pdf", page=2) == {("spec.pdf", 2, None)}
    assert found(roles, document_type="xlsx", page=2) == set()
