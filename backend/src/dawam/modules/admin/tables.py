"""The admin module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base


class SystemSettingRecord(Base):
    """One installation-wide setting (spec §7 ``SystemSetting(key, value)``). A missing
    key means the setting has its default. Never holds a secret in plain text."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(sa.String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
