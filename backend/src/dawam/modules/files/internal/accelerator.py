"""The device the document engines (Docling's layout and table models, RapidOCR) run on.

``auto`` uses an NVIDIA GPU when one is visible and the CPU otherwise; ``cuda`` asked for
on a machine without one also falls back to the CPU (with a warning), so a GPU setting
never stops documents from being indexed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

logger = logging.getLogger(__name__)

DocumentAccelerator = Literal["auto", "cpu", "cuda"]


def cuda_available() -> bool:
    """Whether torch (which Docling runs on) sees a CUDA GPU; false on a CPU-only build."""
    try:
        import torch
    except ImportError:
        return False
    return torch.backends.cuda.is_built() and torch.cuda.is_available()


def accelerator_device(
    accelerator: DocumentAccelerator, gpu: Callable[[], bool] = cuda_available
) -> Literal["cpu", "cuda"]:
    """``cuda`` or ``cpu``: the device the ``accelerator`` setting means on this machine."""
    if accelerator == "cpu":
        return "cpu"
    if gpu():
        return "cuda"
    if accelerator == "cuda":
        logger.warning("document_accelerator is cuda but no CUDA GPU is visible: using the CPU")
    return "cpu"


def accelerator_options(accelerator: DocumentAccelerator, engine: str) -> Any:
    """Docling's accelerator options for ``accelerator``, logging the device ``engine`` uses
    (it also sets RapidOCR's ONNX Runtime provider: CUDA on ``cuda``)."""
    from docling.datamodel.accelerator_options import AcceleratorOptions

    device = accelerator_device(accelerator)
    logger.info("%s runs on %s (document_accelerator=%s)", engine, device, accelerator)
    return AcceleratorOptions(device=device)
