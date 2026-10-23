"""Local token counting for models whose provider has no count endpoint. Never uses the network.

OpenAI-family model names map to a vendored tiktoken encoding (``bpe/``, loaded from disk);
any other model uses a Hugging Face ``tokenizer.json`` only if it is already in the local
Hugging Face cache. Everything else returns ``None`` and the caller estimates.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

BPE_DIR = Path(__file__).parent / "bpe"
_O200K = re.compile(r"^(gpt-4o|gpt-4\.1|gpt-4\.5|gpt-5|chatgpt-4o|o\d)", re.IGNORECASE)
_CL100K = re.compile(r"^(gpt-4|gpt-3\.5|text-embedding-(3-|ada-002))", re.IGNORECASE)
_lock = threading.Lock()


def encoding_name(model: str) -> str | None:
    """The tiktoken encoding of an OpenAI model name (Azure: the deployment name), if known."""
    name = model.rsplit("/", 1)[-1]
    if _O200K.match(name):
        return "o200k_base"
    if _CL100K.match(name):
        return "cl100k_base"
    return None


@lru_cache(maxsize=4)
def _tiktoken(name: str) -> Callable[[str], int] | None:
    try:
        import tiktoken

        with _lock:
            previous = os.environ.get("TIKTOKEN_CACHE_DIR")
            os.environ["TIKTOKEN_CACHE_DIR"] = str(BPE_DIR)
            try:
                encoding = tiktoken.get_encoding(name)
            finally:
                if previous is None:
                    del os.environ["TIKTOKEN_CACHE_DIR"]
                else:
                    os.environ["TIKTOKEN_CACHE_DIR"] = previous
    except Exception as exc:  # a broken install must only cost accuracy
        log.warning("tiktoken %s unavailable (%s)", name, type(exc).__name__)
        return None
    return lambda text: len(encoding.encode(text, disallowed_special=()))


@lru_cache(maxsize=32)
def _huggingface(model: str) -> Callable[[str], int] | None:
    """The cached ``tokenizer.json`` of ``model`` (an ``org/name`` repo id), never downloaded."""
    if "/" not in model:
        return None
    try:
        from huggingface_hub import try_to_load_from_cache
        from tokenizers import Tokenizer

        path = try_to_load_from_cache(model, "tokenizer.json")
        if not isinstance(path, str):
            return None
        tokenizer = Tokenizer.from_file(path)
    except Exception as exc:
        log.warning("Hugging Face tokenizer for %r unavailable (%s)", model, type(exc).__name__)
        return None
    return lambda text: len(tokenizer.encode(text).ids)


def local_counter(model: str) -> Callable[[str], int] | None:
    """A function counting tokens of ``model`` offline, or ``None`` when no tokenizer is known."""
    name = encoding_name(model)
    return _tiktoken(name) if name else _huggingface(model)
