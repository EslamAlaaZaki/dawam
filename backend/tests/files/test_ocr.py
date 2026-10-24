"""OCR of scanned PDFs (#424): the index job reads a PDF page that has too little text of its
own (``OCR_MIN_CHARS_PER_PAGE``) through the OCR engine, so a scan becomes searchable; a PDF
whose every page has enough text skips OCR. The engine is faked at its port
(``Services.ocr_engine``); one real-engine test (marker ``docling``) runs Docling with
RapidOCR on a tiny generated scan."""

from __future__ import annotations

import io
from collections.abc import Sequence
from math import nan
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from pypdf import PdfReader, PdfWriter

from dawam.job_handlers import default_ocr, default_parser
from dawam.modules.files.internal.accelerator import accelerator_device, pdf_pipeline_options
from dawam.modules.files.internal.content import PDF, FileType
from dawam.modules.files.internal.ocr import OCR_MIN_CHARS_PER_PAGE, DoclingOcr, best_reading
from dawam.modules.files.internal.parsing import DoclingParser
from tests.files import test_indexing
from tests.files.test_files import blank_pdf, docx_bytes, system, text_pdf, upload
from tests.files.test_indexing import listed, search
from tests.roles import RoleClients

worker = test_indexing.worker  # the fixture that runs queued jobs on demand

SCANNED = "# Ledger\n\nDormant accounts close after a year.\n\nالحسابات الخاملة تغلق بعد سنة."  # noqa: RUF001
ARABIC = "الحسابات الخاملة"
LONG_LINE = "Dormant accounts are closed after twelve months without activity."


class FakeOcr:
    """Stands in for Docling + RapidOCR: returns canned Markdown, and records the pages it
    was asked to read and the file's ``index_status`` while it ran."""

    def __init__(self, engine: sa.Engine, markdown: str = SCANNED) -> None:
        self.engine = engine
        self.markdown = markdown
        self.pages: list[list[int]] = []
        self.status_while_running: list[str] = []
        self.fail = False

    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        self.pages.append(list(pages))
        with self.engine.connect() as conn:
            self.status_while_running += conn.scalars(
                sa.text("SELECT index_status FROM workspace_files")
            ).all()
        if self.fail:
            raise RuntimeError("engine crashed")
        return self.markdown


@pytest.fixture
def ocr(app) -> FakeOcr:
    fake = FakeOcr(app.state.engine)
    app.state.services.ocr_engine = fake
    return fake


def pdf_of(*pages: bytes) -> bytes:
    """One PDF made of the pages of the given one-page PDFs, in order."""
    writer = PdfWriter()
    for page in pages:
        writer.add_page(PdfReader(io.BytesIO(page)).pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_a_scanned_pdf_is_read_by_ocr_and_becomes_searchable(roles: RoleClients, ocr, worker):
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())

    assert worker() == 1

    done = listed(roles, system_id)["scan.pdf"]
    assert done["index_status"] == "indexed"
    assert done["text_status"] == "extracted"
    assert ocr.pages == [[1]]
    assert ocr.status_while_running == ["ocr"]
    assert [p["document"] for p in search(roles, "dormant")] == ["scan.pdf"]
    assert [p["document"] for p in search(roles, "الخاملة")] == ["scan.pdf"]


def test_a_pdf_with_enough_text_on_every_page_skips_ocr(roles: RoleClients, ocr, worker):
    assert len(LONG_LINE) >= OCR_MIN_CHARS_PER_PAGE
    system_id = system(roles)
    upload(roles, system_id, "typed.pdf", pdf_of(text_pdf(LONG_LINE), text_pdf(LONG_LINE)))
    worker()

    assert ocr.pages == []
    assert listed(roles, system_id)["typed.pdf"]["index_status"] == "indexed"
    assert [p["document"] for p in search(roles, "dormant")] == ["typed.pdf"]


def test_only_the_pages_short_of_text_are_handed_to_ocr(roles: RoleClients, ocr, worker):
    system_id = system(roles)
    upload(roles, system_id, "mixed.pdf", pdf_of(text_pdf(LONG_LINE), blank_pdf(), text_pdf("x")))
    worker()

    assert ocr.pages == [[2, 3]]


def test_a_failing_ocr_engine_keeps_the_text_already_found(roles: RoleClients, ocr, worker):
    ocr.fail = True
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    upload(roles, system_id, "short.pdf", text_pdf("Dormant"))
    worker()

    files = listed(roles, system_id)
    assert files["scan.pdf"]["index_status"] == "indexed"
    assert files["scan.pdf"]["text_status"] == "no_text_found"
    assert files["short.pdf"]["text_status"] == "extracted"
    assert [p["document"] for p in search(roles, "dormant")] == ["short.pdf"]


def test_ocr_reading_nothing_keeps_the_text_already_found(roles: RoleClients, ocr, worker):
    ocr.markdown = "  \n"
    system_id = system(roles)
    upload(roles, system_id, "short.pdf", text_pdf("Dormant"))
    worker()

    assert ocr.pages == [[1]]
    assert [p["document"] for p in search(roles, "dormant")] == ["short.pdf"]


def test_other_file_types_never_go_to_ocr(roles: RoleClients, ocr, worker):
    system_id = system(roles)
    upload(roles, system_id, "owner.docx", docx_bytes())
    upload(roles, system_id, "n.md", b"x")
    worker()

    assert ocr.pages == []


def test_the_settings_turn_ocr_on_or_off(settings):
    assert default_ocr(settings.model_copy(update={"document_ocr": "off"})) is None
    on = settings.model_copy(update={"document_ocr": "rapidocr"})
    assert isinstance(default_ocr(on), DoclingOcr)


def test_the_settings_choose_the_device_of_the_document_engines(settings):
    for accelerator in ("auto", "cpu", "cuda"):
        chosen = settings.model_copy(
            update={
                "document_parser": "docling",
                "document_ocr": "rapidocr",
                "document_accelerator": accelerator,
            }
        )
        ocr, parser = default_ocr(chosen), default_parser(chosen)

        assert isinstance(ocr, DoclingOcr) and ocr.accelerator == accelerator
        assert isinstance(parser, DoclingParser) and parser.accelerator == accelerator


def no_gpu() -> bool:
    return False


def a_gpu() -> bool:
    return True


def test_auto_uses_the_gpu_when_one_is_visible_else_the_cpu():
    assert accelerator_device("auto", a_gpu) == "cuda"
    assert accelerator_device("auto", no_gpu) == "cpu"


def test_cpu_never_uses_the_gpu():
    assert accelerator_device("cpu", a_gpu) == "cpu"


def test_cuda_without_a_gpu_falls_back_to_the_cpu(caplog):
    assert accelerator_device("cuda", a_gpu) == "cuda"
    assert accelerator_device("cuda", no_gpu) == "cpu"
    assert "no CUDA GPU" in caplog.text


def scanned_pdf(text: str) -> bytes:
    """A one-page PDF that is only a picture of ``text``, as a scanner makes it."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1240, 400), "white")
    ImageDraw.Draw(image).text((60, 150), text, fill="black", font=ImageFont.load_default(72))
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=150)
    return buffer.getvalue()


@pytest.mark.docling
def test_real_docling_with_rapidocr_reads_a_scanned_page():
    """OCR for real (needs the layout and RapidOCR model files, cached in CI)."""
    data = scanned_pdf("DORMANT ACCOUNTS")

    markdown = DoclingOcr().recognise(data, [1])

    assert "DORMANT" in markdown.upper()


class PicturesOnlyParser:
    """Docling on a scan without OCR: only image placeholders, no text."""

    types = (PDF,)

    def parse(self, file_type: FileType, data: bytes) -> str:
        return "<!-- image -->\n\n<!-- image -->"


def test_image_placeholders_alone_are_no_text(roles: RoleClients, app, ocr, worker):
    app.state.services.document_parser = PicturesOnlyParser()
    ocr.fail = True
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    assert listed(roles, system_id)["scan.pdf"]["text_status"] == "no_text_found"


class Reading:
    """A Docling result as ``best_reading`` sees it: per-page Markdown and OCR confidence."""

    def __init__(self, pages: dict[int, tuple[str, float]]) -> None:
        self.document = SimpleNamespace(
            pages={no: None for no in pages}, export_to_markdown=self._markdown
        )
        self.confidence = SimpleNamespace(
            pages={no: SimpleNamespace(ocr_score=score) for no, (_, score) in pages.items()}
        )
        self._pages = pages

    def _markdown(self, page_no: int | None = None) -> str:
        if page_no is None:
            return "\n\n".join(text for text, _ in self._pages.values())
        return self._pages[page_no][0]


def test_each_page_keeps_the_language_pass_that_read_it_most_confidently():
    english = Reading({1: ("Dormant accounts", 0.95), 2: ("## ^ U", 0.41), 3: ("typed", nan)})
    arabic = Reading({1: ("## ^ ^ 1 U", 0.52), 2: (ARABIC, 0.9), 3: ("typed", nan)})

    assert best_reading([english, arabic]) == f"Dormant accounts\n\n{ARABIC}\n\ntyped"
    assert best_reading([arabic]) == f"## ^ ^ 1 U\n\n{ARABIC}\n\ntyped"


def test_the_pipelines_pin_the_models_bundled_in_the_image():
    """The Dockerfile bundles only Heron (transformers) and TableFormer accurate: a Docling
    upgrade that changes its defaults must not switch to a model the image lacks."""
    from docling.datamodel.object_detection_engine_options import ObjectDetectionEngineType
    from docling.datamodel.pipeline_options import RapidOcrOptions, TableFormerMode

    for options in (
        pdf_pipeline_options("cpu", "parsing"),
        pdf_pipeline_options("cpu", "OCR", ocr_options=RapidOcrOptions(lang=["en"])),
    ):
        layout = options.layout_options
        assert layout.model_spec.repo_id == "docling-project/docling-layout-heron"
        assert layout.engine_options.engine_type == ObjectDetectionEngineType.TRANSFORMERS
        assert options.table_structure_options.mode == TableFormerMode.ACCURATE
        assert options.accelerator_options.device == "cpu"
