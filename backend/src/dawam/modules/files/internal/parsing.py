"""Layout-aware parsing: a document into Markdown that keeps headings, reading order and
tables (as Markdown tables), for the heading-based chunker.

``DocumentParser`` is the port; ``DoclingParser`` is the self-hosted engine behind it.
Tests fake the port, so the default run never loads Docling or its model files.
"""

from __future__ import annotations

import io
import threading
from typing import Protocol

from .accelerator import DocumentAccelerator, pdf_pipeline_options
from .content import DOCX, PDF, PDF_MAX_PAGES, XLSX, FileType


class DocumentParser(Protocol):
    types: tuple[FileType, ...]
    """The file types this parser reads; any other type keeps the basic extraction."""

    def parse(self, file_type: FileType, data: bytes) -> str:
        """The document as Markdown. Raises when it cannot be read."""
        ...


class DoclingParser:
    """Docling (IBM), self-hosted. OCR is off here: scanned pages are the OCR engine's job
    (a later ticket), so a scan parses to little or no text, as it did before."""

    types = (PDF, DOCX, XLSX)

    def __init__(self, accelerator: DocumentAccelerator = "auto") -> None:
        self.accelerator = accelerator
        self._converter = None
        self._lock = threading.Lock()  # the converter is shared by worker threads

    def _convert(self):
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = pdf_pipeline_options(self.accelerator, "Docling parsing")
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def parse(self, file_type: FileType, data: bytes) -> str:
        from docling.datamodel.base_models import DocumentStream

        name = f"document{file_type.extensions[0]}"
        with self._lock:
            result = self._convert().convert(
                DocumentStream(name=name, stream=io.BytesIO(data)), max_num_pages=PDF_MAX_PAGES
            )
            return result.document.export_to_markdown()
