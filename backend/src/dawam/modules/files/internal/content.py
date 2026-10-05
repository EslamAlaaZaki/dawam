"""Looking inside an upload: what it really is, and the text in it.

Nothing here trusts the client: the type comes from the bytes (magic numbers, the
container structure of DOCX/XLSX, strict UTF-8 for text) and must agree with the
file's extension; only types on the allow-list get through.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass

EXTRACTED_TEXT_MAX_CHARS = 2_000_000
ZIP_MAX_ENTRIES = 5_000
ZIP_MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
"""A DOCX/XLSX that unpacks to more than this is refused (a zip bomb)."""


@dataclass(frozen=True)
class FileType:
    name: str
    mime: str
    extensions: tuple[str, ...]


PDF = FileType("pdf", "application/pdf", (".pdf",))
DOCX = FileType(
    "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    (".docx",),
)
XLSX = FileType(
    "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", (".xlsx",)
)
MARKDOWN = FileType("md", "text/markdown", (".md", ".markdown"))
TEXT = FileType("txt", "text/plain", (".txt",))
PNG = FileType("png", "image/png", (".png",))
JPEG = FileType("jpeg", "image/jpeg", (".jpg", ".jpeg"))
GIF = FileType("gif", "image/gif", (".gif",))
WEBP = FileType("webp", "image/webp", (".webp",))

ALLOWED_TYPES = (PDF, DOCX, XLSX, MARKDOWN, TEXT, PNG, JPEG, GIF, WEBP)
"""The allow-list. SVG and HTML are deliberately absent: they can carry script."""

_IMAGES = (PNG, JPEG, GIF, WEBP)
_BY_EXTENSION = {ext: t for t in ALLOWED_TYPES for ext in t.extensions}


class Unsupported(Exception):
    """The file is not an allowed type, or is not what its extension says."""


def _zip_kind(data: bytes) -> FileType | None:
    if not data.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > ZIP_MAX_ENTRIES:
                raise Unsupported("The file holds too many entries.")
            if sum(info.file_size for info in infos) > ZIP_MAX_UNCOMPRESSED_BYTES:
                raise Unsupported("The file unpacks to an unreasonable size.")
            names = {info.filename for info in infos}
    except zipfile.BadZipFile:
        return None
    if "[Content_Types].xml" not in names:
        return None
    if "word/document.xml" in names:
        return DOCX
    if "xl/workbook.xml" in names:
        return XLSX
    return None


def _binary_kind(data: bytes) -> FileType | None:
    if b"%PDF-" in data[:1024]:
        return PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    return _zip_kind(data)


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def detect(filename: str, data: bytes) -> FileType:
    """The type of ``data``, which must be on the allow-list and agree with the
    extension of ``filename``. Raises ``Unsupported``."""
    dot = filename.rfind(".")
    claimed = _BY_EXTENSION.get(filename[dot:].lower()) if dot >= 0 else None
    if claimed is None:
        raise Unsupported(
            "That file type is not allowed. Upload a PDF, DOCX, XLSX, Markdown, text or "
            "image (PNG, JPEG, GIF or WebP) file."
        )
    found = _binary_kind(data)
    if found is None and claimed in (MARKDOWN, TEXT) and _is_text(data):
        found = claimed
    if found is None or found != claimed:
        raise Unsupported("The file's content does not match its extension.")
    return found


def extract_text(file_type: FileType, data: bytes) -> tuple[str | None, str]:
    """``(text, status)``: ``status`` is ``extracted``, ``no_text_found`` (e.g. a scanned
    PDF, or a document that cannot be read) or ``none`` (images: nothing to extract)."""
    if file_type in _IMAGES:
        return None, "none"
    try:
        text = _extract(file_type, data)
    except Exception:
        return None, "no_text_found"
    text = text.replace("\x00", "").strip()[:EXTRACTED_TEXT_MAX_CHARS]
    if not text:
        return None, "no_text_found"
    return text, "extracted"


def _extract(file_type: FileType, data: bytes) -> str:
    if file_type in (MARKDOWN, TEXT):
        return data.decode("utf-8-sig")
    if file_type is PDF:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return ""
        return _joined(page.extract_text() or "" for page in reader.pages)
    if file_type is DOCX:
        from docx import Document

        document = Document(io.BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            parts.extend("\t".join(cell.text for cell in row.cells) for row in table.rows)
        return _joined(parts)
    if file_type is XLSX:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines: list[str] = []
        size = 0
        for sheet in workbook.worksheets:
            lines.append(f"## {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                line = "\t".join("" if cell is None else str(cell) for cell in row).rstrip("\t")
                if line:
                    lines.append(line)
                    size += len(line)
                if size > EXTRACTED_TEXT_MAX_CHARS:
                    return _joined(lines)
        workbook.close()
        return _joined(lines)
    return ""


def _joined(parts: Iterable[str]) -> str:
    return "\n".join(parts)
