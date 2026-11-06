from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, enum_column, utcnow
from app.core.enums import Decision, ImpactType, LinkType, TargetType, TraceLinkStatus


class ReviewDecision(Base):
    __tablename__ = "review_decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        ForeignKey("finding.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    decision: Mapped[Decision] = mapped_column(enum_column(Decision))
    reason: Mapped[str | None] = mapped_column(Text)
    correction: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TraceLink(Base):
    """Starts SUGGESTED. Only a human decision makes it CONFIRMED."""

    __tablename__ = "trace_link"
    __table_args__ = (Index("ix_trace_link_project_id", "project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    finding_id: Mapped[int] = mapped_column(
        ForeignKey("finding.id", ondelete="CASCADE"), unique=True
    )
    req_key: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[TargetType] = mapped_column(enum_column(TargetType))
    target_key: Mapped[str] = mapped_column(Text)
    impact_type: Mapped[ImpactType] = mapped_column(enum_column(ImpactType))
    link_type: Mapped[LinkType] = mapped_column(enum_column(LinkType))
    status: Mapped[TraceLinkStatus] = mapped_column(
        enum_column(TraceLinkStatus), default=TraceLinkStatus.SUGGESTED
    )
