"""The OCR bake-off (#424): read scanned PDFs with the OCR engine and print a Markdown table
of accuracy, CPU time and wall time, for the PR.

    python tools/ocr_bakeoff.py <folder>

``<folder>`` holds scanned PDFs, each with the true text next to it (``page.pdf`` and
``page.txt``). Accuracy is ``1 - character error rate`` against that text, with whitespace
and Markdown markup collapsed. Needs Docling. ``DAWAM_DOCUMENT_ACCELERATOR`` is not read:
the engine runs on the GPU when one is visible (``auto``).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows
    resource = None

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


def _cpu_seconds() -> float:
    """CPU time of this process and of its finished children; children are not counted on
    Windows, which has no ``resource``."""
    seconds = time.process_time()
    if resource is not None:
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        seconds += children.ru_utime + children.ru_stime
    return seconds


def main(folder: Path) -> None:
    pdfs = sorted(p for p in folder.glob("*.pdf") if p.with_suffix(".txt").exists())
    print("| Backend | File | Accuracy | CPU seconds | Wall seconds |")
    print("| --- | --- | --- | --- | --- |")
    for backend, engine in [("rapidocr", DoclingOcr())]:
        for pdf in pdfs:
            data = pdf.read_bytes()
            cpu, wall = _cpu_seconds(), time.perf_counter()
            try:
                found = engine.recognise(data, pages_needing_ocr(data))
            except Exception as exc:  # report it and go on with the next file
                print(f"| {backend} | {pdf.name} | failed: {exc} | | |")
                continue
            cpu, wall = _cpu_seconds() - cpu, time.perf_counter() - wall
            truth = pdf.with_suffix(".txt").read_text(encoding="utf-8")
            accuracy = _accuracy(found, truth)
            print(f"| {backend} | {pdf.name} | {accuracy:.1%} | {cpu:.1f} | {wall:.1f} |")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
