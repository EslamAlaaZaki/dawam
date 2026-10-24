"""OCR: the text of a scanned PDF page, read from its image, so a scan becomes searchable.

``OcrEngine`` is the port; ``DoclingOcr`` is the self-hosted engine behind it: the Docling
pipeline with a pluggable OCR backend, RapidOCR (PP-OCR models on ONNX Runtime, CPU) by
default and Tesseract as the fallback. Only the pages with too little text of their own are
read this way (``pages_needing_ocr``). Tests fake the port, so the default run never loads
Docling or its model files.

The defaults are code constants until the Document AI settings exist.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from .content import PDF_MAX_PAGES

OCR_MIN_CHARS_PER_PAGE = 50
"""A PDF page with fewer characters of its own text than this is read by OCR."""
OCR_MAX_PAGES = 200
"""A PDF with more pages than this to OCR keeps the text it has: OCR is slow on a CPU."""
RAPIDOCR_LANGUAGE = "arabic"
"""RapidOCR runs one recognizer per run; PP-OCR's Arabic one also reads Latin script, so
it covers the Arabic and English documents we expect."""
TESSERACT_LANGUAGES = ["ara", "eng"]
RAPIDOCR_MODELS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "rapidocr"
"""Where RapidOCR keeps its model files (cached in CI), instead of its package folder."""

OcrBackend = Literal["rapidocr", "tesseract"]


class OcrEngine(Protocol):
    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        """The PDF ``data`` as Markdown, with the text of ``pages`` (1-based: those short of
        text of their own) read from their images. Raises when it cannot be read."""
        ...


def pages_needing_ocr(data: bytes) -> list[int]:
    """The 1-based numbers of the PDF's pages with fewer than ``OCR_MIN_CHARS_PER_PAGE``
    characters of text; none for a PDF over the page caps. Raises when it cannot be read."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > PDF_MAX_PAGES:
        return []
    short = [
        number
        for number, page in enumerate(reader.pages, start=1)
        if len((page.extract_text() or "").strip()) < OCR_MIN_CHARS_PER_PAGE
    ]
    return short if len(short) <= OCR_MAX_PAGES else []


class DoclingOcr:
    """Docling with OCR on. Its default OCR mode reads only the regions of a page that have
    no PDF text, so the pages with text of their own keep it (``pages`` is advisory here)."""

    def __init__(self, backend: OcrBackend = "rapidocr") -> None:
        self.backend = backend
        self._converter = None
        self._lock = threading.Lock()  # the converter is shared by worker threads

    def _ocr_options(self):
        if self.backend == "tesseract":
            from docling.datamodel.pipeline_options import TesseractCliOcrOptions

            return TesseractCliOcrOptions(lang=TESSERACT_LANGUAGES)
        from docling.datamodel.pipeline_options import RapidOcrOptions

        try:
            RAPIDOCR_MODELS.mkdir(parents=True, exist_ok=True)
            params = {"Global.model_root_dir": str(RAPIDOCR_MODELS)}
        except OSError:  # no writable home (e.g. a system user): RapidOCR's own folder
            params = {}
        return RapidOcrOptions(
            lang=[RAPIDOCR_LANGUAGE], backend="onnxruntime", rapidocr_params=params
        )

    def _convert(self):
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(
                do_ocr=True, do_table_structure=True, ocr_options=self._ocr_options()
            )
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        from docling.datamodel.base_models import DocumentStream

        with self._lock:
            result = self._convert().convert(
                DocumentStream(name="document.pdf", stream=io.BytesIO(data)),
                max_num_pages=PDF_MAX_PAGES,
            )
            return result.document.export_to_markdown()
