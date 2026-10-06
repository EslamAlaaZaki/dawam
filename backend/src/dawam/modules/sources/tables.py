"""The sources module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

NAME_MAX_LENGTH = 200
CODE_MAX_LENGTH = 24
DESCRIPTION_MAX_LENGTH = 4000
OWNER_MAX_LENGTH = 200
TAG_MAX_LENGTH = 40
MAX_TAGS = 20
SCD_HINT_MAX_LENGTH = 200
CLASSIFICATIONS = ("master", "transactional", "reference", "log", "landing")

CODE_CONSTRAINT = "uq_source_systems_workspace_id"
"""Violated by a System Code another Source System of the Workspace already has."""


class SourceSystemRecord(Base):
    __tablename__ = "source_systems"
    __table_args__ = (
        sa.UniqueConstraint("workspace_id", "code"),
        sa.CheckConstraint("status IN ('present', 'deleted')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    code: Mapped[str] = mapped_column(sa.String(CODE_MAX_LENGTH))
    """The System Code: unique per Workspace, identifier-safe."""
    description: Mapped[str] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH))
    business_owner: Mapped[str] = mapped_column(sa.String(OWNER_MAX_LENGTH))
    technical_owner: Mapped[str] = mapped_column(sa.String(OWNER_MAX_LENGTH))
    status: Mapped[str] = mapped_column(sa.String(16))
    """``present`` or ``deleted`` (a soft delete; spec §7)."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""


HOST_MAX_LENGTH = 253
IDENTIFIER_MAX_LENGTH = 128
SYSTEM_CONNECTION_CONSTRAINT = "uq_connections_source_system_id"


class ConnectionRecord(Base):
    """A Source System's live database Connection (one per system, spec story 40)."""

    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_systems.id", ondelete="CASCADE"), unique=True
    )
    engine: Mapped[str] = mapped_column(sa.String(32))
    host: Mapped[str] = mapped_column(sa.String(HOST_MAX_LENGTH))
    port: Mapped[int] = mapped_column()
    database: Mapped[str] = mapped_column(sa.String(IDENTIFIER_MAX_LENGTH))
    username: Mapped[str] = mapped_column(sa.String(IDENTIFIER_MAX_LENGTH))
    secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    """The password sealed with ``dawam.platform.crypto.SecretBox`` (AES-256-GCM, context
    ``connection.password``); ``None`` when the database needs none. Never returned."""
    options: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    allowed_schemas: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.String(IDENTIFIER_MAX_LENGTH)))
    can_write: Mapped[bool | None] = mapped_column()
    """From the last successful test: could the user change data? ``None``: never tested."""
    last_tested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


# -- Source Objects and Snapshots (spec §7 "Source identity vs Snapshots") ------------

SOURCE_OBJECT_STATUSES = ("present", "source_removed", "out_of_scope", "deleted")
_STATUS_CHECK = "status IN ('present', 'source_removed', 'out_of_scope', 'deleted')"
HASH_LENGTH = 64
"""A SHA-256 hex digest."""


class SrcDbSchemaRecord(Base):
    """A Database Schema's stable identity in a Source System."""

    __tablename__ = "src_db_schemas"
    __table_args__ = (
        sa.UniqueConstraint("source_system_id", "name"),
        sa.CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_systems.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16))
    """``present``, ``source_removed``, ``out_of_scope`` or ``deleted`` (spec §7)."""


class SrcTableRecord(Base):
    """A table's or view's stable identity: descriptions, PII, relationships and mappings
    attach here, never to a Snapshot."""

    __tablename__ = "src_tables"
    __table_args__ = (
        sa.UniqueConstraint("db_schema_id", "name"),
        sa.CheckConstraint(_STATUS_CHECK, name="status"),
        sa.CheckConstraint("kind IN ('table', 'view')", name="kind"),
        sa.CheckConstraint(
            "classification IS NULL OR classification IN "
            "('master', 'transactional', 'reference', 'log', 'landing')",
            name="classification",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    db_schema_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_db_schemas.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(16))
    current_definition: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """As of the latest Snapshot that had it (kind, comment, view definition hash)."""
    classification: Mapped[str | None] = mapped_column(sa.String(16))
    """``master``, ``transactional``, ``reference``, ``log`` or ``landing`` (an Editor's label)."""
    scd_hint: Mapped[str | None] = mapped_column(sa.String(SCD_HINT_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH))
    tags: Mapped[list[str]] = mapped_column(
        sa.ARRAY(sa.String(TAG_MAX_LENGTH)), default=list, server_default="{}"
    )
    is_sensitive: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    status: Mapped[str] = mapped_column(sa.String(16))
    version: Mapped[int] = mapped_column()
    """Goes up by one whenever the current definition (or the name's case) changes, and
    on every edit of the enhancements (optimistic concurrency, spec §8.3)."""


class SrcColumnRecord(Base):
    __tablename__ = "src_columns"
    __table_args__ = (
        sa.UniqueConstraint("table_id", "name"),
        sa.CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("src_tables.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(sa.Text)
    current_definition: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """As of the latest Snapshot that had it: data type, nullability, key, default."""
    description: Mapped[str | None] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH))
    tags: Mapped[list[str]] = mapped_column(
        sa.ARRAY(sa.String(TAG_MAX_LENGTH)), default=list, server_default="{}"
    )
    is_sensitive: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    pii_category: Mapped[str | None] = mapped_column(sa.String(32))
    """Set when an Editor confirms a PII finding (spec story 135)."""
    status: Mapped[str] = mapped_column(sa.String(16))
    version: Mapped[int] = mapped_column()


PII_STATUSES = ("suggested", "confirmed", "dismissed")
PII_CATEGORIES = ("direct_identifier", "quasi_identifier", "sensitive", "financial")
_PII_STATUS_CHECK = "status IN ('suggested', 'confirmed', 'dismissed')"


class PiiFindingRecord(Base):
    """A suspected PII column (spec §6.12). One per column and rule; it attaches to the
    stable Source Column, so a decision survives Snapshots. It never holds a value."""

    __tablename__ = "pii_findings"
    __table_args__ = (
        sa.UniqueConstraint("src_column_id", "rule"),
        sa.CheckConstraint(_PII_STATUS_CHECK, name="status"),
        sa.CheckConstraint(
            "category IN ('direct_identifier', 'quasi_identifier', 'sensitive', 'financial')",
            name="category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    src_column_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_columns.id", ondelete="CASCADE"), index=True
    )
    rule: Mapped[str] = mapped_column(sa.String(48))
    """The name rule that fired (``national_id``, ...; ``value-at-query`` later)."""
    category: Mapped[str] = mapped_column(sa.String(32))
    confidence: Mapped[float] = mapped_column()
    evidence: Mapped[str] = mapped_column(sa.String(500))
    status: Mapped[str] = mapped_column(sa.String(16))
    """``suggested`` (in the review queue), ``confirmed`` or ``dismissed``."""
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("snapshots.id", ondelete="SET NULL")
    )
    """The Snapshot whose scan found it."""
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class SrcRoutineRecord(Base):
    """A stored procedure's or function's stable identity. ``signature`` (the argument
    list) tells PostgreSQL overloads apart; it is ``""`` for engines without them."""

    __tablename__ = "src_routines"
    __table_args__ = (
        sa.UniqueConstraint("db_schema_id", "name", "kind", "signature"),
        sa.CheckConstraint(_STATUS_CHECK, name="status"),
        sa.CheckConstraint("kind IN ('procedure', 'function')", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    db_schema_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_db_schemas.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(16))
    signature: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16))


class DefinitionTextRecord(Base):
    """View and routine text, stored once per content hash."""

    __tablename__ = "definition_texts"

    hash: Mapped[str] = mapped_column(sa.String(HASH_LENGTH), primary_key=True)
    text: Mapped[str] = mapped_column(sa.Text)


class SnapshotRecord(Base):
    """An immutable, point-in-time capture of a Source System's metadata."""

    __tablename__ = "snapshots"
    __table_args__ = (
        sa.CheckConstraint("origin IN ('connection', 'import')", name="origin"),
        sa.Index(
            "uq_snapshots_latest",
            "source_system_id",
            unique=True,
            postgresql_where=sa.text("is_latest"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_systems.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(sa.String(16))
    job_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("jobs.id", ondelete="SET NULL"))
    taken_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    is_latest: Mapped[bool] = mapped_column()
    content_hash: Mapped[str] = mapped_column(sa.String(HASH_LENGTH))
    """Of everything captured: an extraction with the latest Snapshot's hash creates no
    new Snapshot."""
    schema_count: Mapped[int] = mapped_column()
    table_count: Mapped[int] = mapped_column()
    column_count: Mapped[int] = mapped_column()
    routine_count: Mapped[int] = mapped_column()


def _snapshot_id_column() -> Mapped[uuid.UUID]:
    return mapped_column(sa.ForeignKey("snapshots.id", ondelete="CASCADE"), primary_key=True)


def _src_table_key_column() -> Mapped[uuid.UUID]:
    return mapped_column(sa.ForeignKey("src_tables.id", ondelete="CASCADE"), primary_key=True)


class SnapshotDbSchemaRecord(Base):
    __tablename__ = "snapshot_db_schemas"

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_db_schema_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_db_schemas.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(sa.Text)


class SnapshotTableRecord(Base):
    __tablename__ = "snapshot_tables"

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_table_id: Mapped[uuid.UUID] = _src_table_key_column()
    db_schema: Mapped[str] = mapped_column(sa.Text)
    """The Database Schema's name as it was in this Snapshot."""
    name: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(16))
    view_definition_hash: Mapped[str | None] = mapped_column(sa.ForeignKey("definition_texts.hash"))
    row_estimate: Mapped[int | None] = mapped_column(sa.BigInteger)
    comment: Mapped[str | None] = mapped_column(sa.Text)


class SnapshotColumnRecord(Base):
    __tablename__ = "snapshot_columns"

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_column_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_columns.id", ondelete="CASCADE"), primary_key=True
    )
    src_table_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_tables.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    ordinal: Mapped[int] = mapped_column()
    data_type: Mapped[str] = mapped_column(sa.Text)
    is_nullable: Mapped[bool] = mapped_column()
    is_pk: Mapped[bool] = mapped_column()
    default: Mapped[str | None] = mapped_column(sa.Text)
    comment: Mapped[str | None] = mapped_column(sa.Text)


class SnapshotConstraintRecord(Base):
    __tablename__ = "snapshot_constraints"
    __table_args__ = (sa.CheckConstraint("type IN ('pk', 'fk', 'unique')", name="type"),)

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_table_id: Mapped[uuid.UUID] = _src_table_key_column()
    name: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    type: Mapped[str] = mapped_column(sa.String(16))
    columns: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.Text))
    ref_table_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("src_tables.id", ondelete="CASCADE")
    )
    """A foreign key's referenced table, when that table is in this Snapshot."""
    ref_db_schema: Mapped[str | None] = mapped_column(sa.Text)
    ref_table: Mapped[str | None] = mapped_column(sa.Text)
    ref_columns: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.Text))


class SnapshotIndexRecord(Base):
    __tablename__ = "snapshot_indexes"

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_table_id: Mapped[uuid.UUID] = _src_table_key_column()
    name: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    columns: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.Text))
    is_unique: Mapped[bool] = mapped_column()


class SnapshotRoutineRecord(Base):
    __tablename__ = "snapshot_routines"

    snapshot_id: Mapped[uuid.UUID] = _snapshot_id_column()
    src_routine_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("src_routines.id", ondelete="CASCADE"), primary_key=True
    )
    db_schema: Mapped[str] = mapped_column(sa.Text)
    name: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(16))
    signature: Mapped[str] = mapped_column(sa.Text)
    definition_hash: Mapped[str | None] = mapped_column(sa.ForeignKey("definition_texts.hash"))
