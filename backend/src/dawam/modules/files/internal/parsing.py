"""Layout-aware parsing: a document into Markdown that keeps headings, reading order and
tables (as Markdown tables), for the heading-based chunker.

``DocumentParser`` is the port; ``DoclingParser`` is the self-hosted engine behind it.
Tests fake the port, so the default run never loads Docling or its model files.
"""

from __future__ import annotations

import io
from typing import Protocol

from .content import DOCX, PDF, XLSX, FileType


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

    def __init__(self) -> None:
        self._converter = None

    def _convert(self):
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def parse(self, file_type: FileType, data: bytes) -> str:
        from docling.datamodel.base_models import DocumentStream

        name = f"document{file_type.extensions[0]}"
        result = self._convert().convert(DocumentStream(name=name, stream=io.BytesIO(data)))
        return result.document.export_to_markdown()
