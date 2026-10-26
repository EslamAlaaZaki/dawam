"""Layout-aware parsing (#364): the index job parses PDF, DOCX and XLSX into Markdown with
headings and tables, and the heading-based chunker cuts it into sections. The parser is
faked at its port (``Services.document_parser``); one real-engine test (marker ``docling``)
runs Docling on a tiny PDF."""

from __future__ import annotations

import io

import pytest
import sqlalchemy as sa
from docx import Document

from dawam.modules.files.internal import content
from dawam.modules.files.internal.content import DOCX, PDF, TEXT, FileType
from dawam.modules.files.internal.parsing import DoclingParser
from tests.files import test_indexing
from tests.files.test_files import system, text_pdf, upload
from tests.files.test_indexing import listed
from tests.roles import RoleClients

worker = test_indexing.worker  # the fixture that runs queued jobs on demand

MARKDOWN = """# Loans

Loans are booked daily.

## Status codes

| Code | Meaning |
| --- | --- |
| A | Active |
| D | Dormant |

## Retention

Records are kept for seven years.
"""


class FakeParser:
    """Stands in for Docling: returns canned Markdown and records what it was given."""

    types = (PDF, DOCX)

    def __init__(self, markdown: str = MARKDOWN) -> None:
        self.markdown = markdown
        self.seen: list[FileType] = []
        self.fail = False

    def parse(self, file_type: FileType, data: bytes) -> str:
        self.seen.append(file_type)
        if self.fail:
            raise RuntimeError("engine crashed")
        return self.markdown


@pytest.fixture
def parser(app) -> FakeParser:
    fake = FakeParser()
    app.state.services.document_parser = fake
    return fake


def sections(roles: RoleClients) -> dict[str, str]:
    with roles.app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT section, content FROM document_chunks")).all()
    return {section: text for section, text in rows}


def test_a_pdf_with_headings_yields_one_section_per_heading(roles: RoleClients, parser, worker):
    system_id = system(roles)
    upload(roles, system_id, "loans.pdf", text_pdf("ignored by the fake"))

    assert worker() == 1

    assert listed(roles, system_id)["loans.pdf"]["index_status"] == "indexed"
    assert parser.seen == [PDF]
    assert set(sections(roles)) == {"Loans", "Status codes", "Retention"}


def test_a_table_comes_out_as_a_markdown_table(roles: RoleClients, parser, worker):
    system_id = system(roles)
    upload(roles, system_id, "loans.pdf", text_pdf("x"))
    worker()

    table = sections(roles)["Status codes"]

    assert "| Code | Meaning |" in table
    assert "| --- | --- |" in table
    assert "| D | Dormant |" in table


def test_types_the_parser_does_not_handle_keep_their_own_text(roles: RoleClients, parser, worker):
    system_id = system(roles)
    upload(roles, system_id, "n.md", b"# Notes\nplain")
    worker()

    assert parser.seen == []
    assert set(sections(roles)) == {"Notes"}
    assert TEXT not in parser.types


def test_a_crashing_parser_falls_back_to_the_basic_extraction(roles: RoleClients, parser, worker):
    parser.fail = True
    system_id = system(roles)
    upload(roles, system_id, "loans.pdf", text_pdf("Dormant accounts"))
    worker()

    assert listed(roles, system_id)["loans.pdf"]["index_status"] == "indexed"
    assert "Dormant accounts" in "".join(sections(roles).values())


def test_a_document_over_a_work_cap_skips_the_parser(
    roles: RoleClients, parser, worker, monkeypatch
):
    monkeypatch.setattr(content, "DOCX_MAX_PARAGRAPHS", 3)
    document = Document()
    for n in range(10):
        document.add_paragraph(f"para{n}")
    buffer = io.BytesIO()
    document.save(buffer)
    system_id = system(roles)
    upload(roles, system_id, "big.docx", buffer.getvalue())
    worker()

    assert parser.seen == []  # the basic extraction read it, and stopped at the cap
    assert listed(roles, system_id)["big.docx"]["index_status"] == "indexed"
    assert "para9" not in "".join(sections(roles).values())


def layout_pdf() -> bytes:
    """A one-page PDF: a large bold heading, a sentence, and a ruled 2x2 table."""
    lines = [
        "BT /F2 28 Tf 72 720 Td (Status codes) Tj ET",
        "BT /F1 12 Tf 72 680 Td (Codes of the loans ledger.) Tj ET",
    ]
    for x in (72, 222, 372):
        lines.append(f"{x} 630 m {x} 540 l S")
    for y in (630, 585, 540):
        lines.append(f"72 {y} m 372 {y} l S")
    cells = [("Code", 80, 605), ("Meaning", 230, 605), ("D", 80, 560), ("Dormant", 230, 560)]
    lines += [f"BT /F1 12 Tf {x} {y} Td ({t}) Tj ET" for t, x, y in cells]
    stream = "\n".join(lines).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@pytest.mark.docling
def test_real_docling_keeps_a_heading_and_a_table_in_a_pdf():
    """The PDF layout path for real (needs the layout models, cached in CI)."""
    markdown = DoclingParser().parse(PDF, layout_pdf())

    assert "# Status codes" in markdown
    assert "| Code" in markdown and "Dormant" in markdown


@pytest.mark.docling
def test_real_docling_keeps_a_heading_and_a_table():
    """Runs the real engine on a tiny generated DOCX (no model files needed for DOCX; a
    PDF would need the layout models, cached in CI). Run with ``pytest -m docling``."""
    document = Document()
    document.add_heading("Status codes", level=1)
    document.add_paragraph("Codes used by the loans ledger.")
    table = document.add_table(rows=2, cols=2)
    for cell, text in zip(table._cells, ["Code", "Meaning", "D", "Dormant"], strict=True):
        cell.text = text
    buffer = io.BytesIO()
    document.save(buffer)

    markdown = DoclingParser().parse(DOCX, buffer.getvalue())

    assert "# Status codes" in markdown
    assert "| Code" in markdown and "Dormant" in markdown
