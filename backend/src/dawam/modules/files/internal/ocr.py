"""OCR: the text of a scanned PDF page, read from its image, so a scan becomes searchable.

``OcrEngine`` is the port; ``DoclingOcr`` is the self-hosted engine behind it: the Docling
pipeline with RapidOCR (PP-OCR models on ONNX Runtime, on the CPU or an NVIDIA GPU). Only
the pages with too little text of their own are read this way (``pages_needing_ocr``). Tests
fake the port, so the default run never loads Docling or its model files.

The defaults are code constants until the Document AI settings exist.
"""

from __future__ import annotations

import io
import math
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .accelerator import DocumentAccelerator, accelerator_options
from .content import PDF_MAX_PAGES

OCR_MIN_CHARS_PER_PAGE = 50
"""A PDF page with fewer characters of its own text than this is read by OCR."""
OCR_MAX_PAGES = 200
"""A PDF with more pages than this to OCR keeps the text it has: OCR is slow on a CPU."""
RAPIDOCR_LANGUAGES = ("en", "arabic")
"""RapidOCR runs one recognizer per run, and none reads both scripts: PP-OCR's Arabic one
turns Latin into junk. So a page is read once per language (PP-OCRv6 multilingual for
English, PP-OCRv5 Arabic) and the reading with the higher mean OCR confidence is kept."""
RAPIDOCR_MODELS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "rapidocr"
"""Where RapidOCR downloads its model files (cached in CI), instead of its package folder.
Unused when ``DOCLING_ARTIFACTS_PATH`` is set (the image): the models are read from there."""


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


def _ocr_score(result: Any, page_no: int) -> float:
    """The mean OCR confidence of a page in a Docling result; -1 when OCR read nothing."""
    page = result.confidence.pages.get(page_no)
    score = page.ocr_score if page is not None else math.nan
    return -1.0 if math.isnan(score) else float(score)


def best_reading(results: Sequence[Any]) -> str:
    """One Markdown document from several Docling results of the same PDF (one per OCR
    language): each page comes from the result that read it with the most confidence (on a
    tie, the first)."""
    first = results[0]
    if len(results) == 1:
        return first.document.export_to_markdown()
    parts = []
    for page_no in sorted(first.document.pages):
        best = max(results, key=lambda result: _ocr_score(result, page_no))
        parts.append(best.document.export_to_markdown(page_no=page_no))
    return "\n\n".join(part for part in parts if part.strip())


class DoclingOcr:
    """Docling with RapidOCR on. Its default OCR mode reads only the regions of a page that
    have no PDF text, so the pages with text of their own keep it (``pages`` is advisory
    here). The document is read once per language in ``RAPIDOCR_LANGUAGES``."""

    def __init__(self, accelerator: DocumentAccelerator = "auto") -> None:
        self.accelerator = accelerator
        self._converters: list[Any] | None = None
        self._lock = threading.Lock()  # the converters are shared by worker threads

    def _ocr_options(self) -> list[Any]:
        from docling.datamodel.pipeline_options import RapidOcrOptions

        try:
            RAPIDOCR_MODELS.mkdir(parents=True, exist_ok=True)
            params = {"Global.model_root_dir": str(RAPIDOCR_MODELS)}
        except OSError:  # no writable home (e.g. a system user): RapidOCR's own folder
            params = {}
        return [
            RapidOcrOptions(lang=[language], backend="onnxruntime", rapidocr_params=params)
            for language in RAPIDOCR_LANGUAGES
        ]

    def _convert(self) -> list[Any]:
        if self._converters is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            accelerator = accelerator_options(self.accelerator, "OCR (Docling + RapidOCR)")
            self._converters = [
                DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(
                            pipeline_options=PdfPipelineOptions(
                                do_ocr=True,
                                do_table_structure=True,
                                ocr_options=ocr,
                                accelerator_options=accelerator,
                            )
                        )
                    }
                )
                for ocr in self._ocr_options()
            ]
        return self._converters

    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        from docling.datamodel.base_models import DocumentStream

        with self._lock:
            results = [
                converter.convert(
                    DocumentStream(name="document.pdf", stream=io.BytesIO(data)),
                    max_num_pages=PDF_MAX_PAGES,
                )
                for converter in self._convert()
            ]
            return best_reading(results)
