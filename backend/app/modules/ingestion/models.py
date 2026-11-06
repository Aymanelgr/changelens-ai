from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, enum_column, utcnow
from app.core.enums import ExtractionStatus


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DocumentVersion(Base):
    """Immutable once created (DATA-004): a re-import adds a version, never edits one."""

    __tablename__ = "document_version"
    __table_args__ = (UniqueConstraint("document_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    file_type: Mapped[str] = mapped_column(String(10))
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(enum_column(ExtractionStatus))
    storage_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    document: Mapped[Document] = relationship()


class Segment(Base):
    __tablename__ = "segment"
    __table_args__ = (
        Index("ix_segment_document_version_id", "document_version_id"),
        Index("ix_segment_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_version.id", ondelete="CASCADE")
    )
    page: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(Text)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    # Kept up to date by PostgreSQL itself, so it can never disagree with the text.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('french', coalesce(section_path, '') || ' ' || text)", persisted=True
        ),
    )
    # No fixed dimension, and the model name beside each vector: vectors from two models are
    # never compared by accident, and a model can be swapped without a schema change.
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(String(200))

    document_version: Mapped[DocumentVersion] = relationship()
