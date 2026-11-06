from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, enum_column, utcnow
from app.core.enums import ChangeRequestStatus


class RequirementSet(Base):
    __tablename__ = "requirement_set"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RequirementVersion(Base):
    __tablename__ = "requirement_version"
    __table_args__ = (UniqueConstraint("requirement_set_id", "req_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_set_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_set.id", ondelete="CASCADE")
    )
    req_key: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(500), default="")
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))


class ChangeRequest(Base):
    __tablename__ = "change_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    set_before_id: Mapped[int] = mapped_column(ForeignKey("requirement_set.id", ondelete="CASCADE"))
    set_after_id: Mapped[int] = mapped_column(ForeignKey("requirement_set.id", ondelete="CASCADE"))
    status: Mapped[ChangeRequestStatus] = mapped_column(
        enum_column(ChangeRequestStatus), default=ChangeRequestStatus.DRAFT
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
