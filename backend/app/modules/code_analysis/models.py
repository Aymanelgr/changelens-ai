from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, enum_column, utcnow
from app.core.enums import SymbolKind


class RepositorySnapshot(Base):
    """One immutable import of a code archive."""

    __tablename__ = "repository_snapshot"
    __table_args__ = (UniqueConstraint("project_id", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    sha256: Mapped[str] = mapped_column(String(64))
    archive_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    file_count: Mapped[int] = mapped_column(Integer)
    python_file_count: Mapped[int] = mapped_column(Integer)
    parse_error_count: Mapped[int] = mapped_column(Integer, default=0)
    unresolved_call_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_sensitive_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CodeSymbol(Base):
    __tablename__ = "code_symbol"
    __table_args__ = (
        Index("ix_code_symbol_snapshot_id", "snapshot_id"),
        Index("ix_code_symbol_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("repository_snapshot.id", ondelete="CASCADE")
    )
    file_path: Mapped[str] = mapped_column(String(500))
    qualified_name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[SymbolKind] = mapped_column(enum_column(SymbolKind))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    # The source itself is stored: evidence must stay verifiable after the archive is gone.
    source_text: Mapped[str] = mapped_column(Text)
    number_literals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    text_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Underscores become spaces, so HIGH_VALUE_LIMIT is findable by the words "high" and "limit".
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('french', replace(qualified_name || ' ' || file_path || ' ' "
            "|| coalesce(docstring, '') || ' ' || source_text, '_', ' '))",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    # Hash of the text that was embedded: an unchanged function reuses its vector (step 5).
    embedding_sha256: Mapped[str | None] = mapped_column(String(64))


class CallEdge(Base):
    """One call site. callee_id stays NULL when static analysis cannot resolve the target."""

    __tablename__ = "call_edge"
    __table_args__ = (
        Index("ix_call_edge_snapshot_id", "snapshot_id"),
        Index("ix_call_edge_caller_id", "caller_id"),
        Index("ix_call_edge_callee_id", "callee_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("repository_snapshot.id", ondelete="CASCADE")
    )
    caller_id: Mapped[int] = mapped_column(ForeignKey("code_symbol.id", ondelete="CASCADE"))
    callee_id: Mapped[int | None] = mapped_column(ForeignKey("code_symbol.id", ondelete="CASCADE"))
    callee_name: Mapped[str] = mapped_column(String(500))
    line: Mapped[int] = mapped_column(Integer)
    resolved: Mapped[bool] = mapped_column(Boolean)
    external: Mapped[bool] = mapped_column(Boolean, default=False)
