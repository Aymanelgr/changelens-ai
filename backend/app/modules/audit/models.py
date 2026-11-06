from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow


class AuditEvent(Base):
    """Append-only (FR-016). A database trigger, created in the migrations, refuses UPDATE
    and DELETE, so the guarantee holds even against a bug in this application.

    project_id and actor_id are deliberately not foreign keys: the history of who did
    what must survive the deletion of the project or the user.
    """

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_project_id_id", "project_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column()
    actor_id: Mapped[int | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
