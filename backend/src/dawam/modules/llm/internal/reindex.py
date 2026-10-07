"""Whether the embedding index is stale (spec §6.18 "Embedding changes").

Switching the embedding model, or a model whose vector dimension differs, means the
stored vectors no longer fit: the flag below records that re-indexing is needed (the
re-index job itself comes with document search).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ..tables import SettingsRecord

MODEL_CHANGED = "embedding_model_changed"
DIMENSION_CHANGED = "embedding_dimension_changed"


def flag(settings: SettingsRecord, reason: str, now: datetime) -> None:
    settings.reindex_needed = True
    settings.reindex_reason = reason
    settings.reindex_flagged_at = now


def note_assigned(
    settings: SettingsRecord, model_id: uuid.UUID | None, dimension: int | None, now: datetime
) -> None:
    """The embedding role was set to ``model_id`` (``None``: cleared)."""
    if model_id is not None and model_id != settings.embedding_model_id:
        flag(settings, MODEL_CHANGED, now)
    settings.embedding_model_id = model_id
    settings.embedding_dimension = dimension if model_id is not None else None


def note_tested(
    settings: SettingsRecord, model_id: uuid.UUID, dimension: int | None, now: datetime
) -> None:
    """ "Test connection" found ``dimension`` for a model: if it is the assigned embedding
    model and the dimension differs from the one its vectors have, re-indexing is needed."""
    if settings.embedding_model_id != model_id or dimension is None:
        return
    if settings.embedding_dimension is not None and settings.embedding_dimension != dimension:
        flag(settings, DIMENSION_CHANGED, now)
    settings.embedding_dimension = dimension
