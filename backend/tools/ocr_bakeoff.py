"""The OCR bake-off (#424): read scanned PDFs with each OCR backend and print a Markdown table
of accuracy and CPU time, for the PR.

    python tools/ocr_bakeoff.py <folder>

``<folder>`` holds scanned PDFs, each with the true text next to it (``page.pdf`` and
``page.txt``). Accuracy is ``1 - character error rate`` against that text, with whitespace
and Markdown markup collapsed. Needs Docling and, for Tesseract, the ``tesseract`` binary
with the ``ara`` and ``eng`` languages.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from dawam.modules.files.internal.ocr import DoclingOcr, pages_needing_ocr


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[#|*_`>-]+", " ", text).split())


def _accuracy(found: str, truth: str) -> float:
    """``1 - (edit distance / length of the truth)``, floored at 0."""
    a, b = _plain(found), _plain(truth)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return max(0.0, 1 - previous[-1] / max(len(b), 1))


def main(folder: Path) -> None:
    pdfs = sorted(p for p in folder.glob("*.pdf") if p.with_suffix(".txt").exists())
    print("| Backend | File | Accuracy | CPU seconds |\n| --- | --- | --- | --- |")
    for backend in ("rapidocr", "tesseract"):
        engine = DoclingOcr(backend)  # type: ignore[arg-type]
        for pdf in pdfs:
            data = pdf.read_bytes()
            start = time.process_time()
            try:
                found = engine.recognise(data, pages_needing_ocr(data))
            except Exception as exc:  # report it and go on with the next file
                print(f"| {backend} | {pdf.name} | failed: {exc} | |")
                continue
            seconds = time.process_time() - start
            truth = pdf.with_suffix(".txt").read_text(encoding="utf-8")
            print(f"| {backend} | {pdf.name} | {_accuracy(found, truth):.1%} | {seconds:.1f} |")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
