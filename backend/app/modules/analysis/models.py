from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime, enum_column, utcnow
from app.core.enums import (
    ChangeRequestStatus,
    EvidenceRole,
    EvidenceSource,
    FindingStatus,
    ImpactType,
    LinkType,
    LlmVerdict,
    RunInputType,
    RunStatus,
    Support,
    TargetType,
    TestOrigin,
    TestStatus,
)
from app.modules.code_analysis.models import CodeSymbol
from app.modules.ingestion.models import Segment
from app.modules.requirements.models import RequirementVersion


class AnalysisRun(Base):
    __tablename__ = "analysis_run"
    __table_args__ = (
        # One active run per change request, enforced by the database. An application-level
        # check alone would let two simultaneous clicks both pass.
        Index(
            "uq_analysis_run_one_active_per_change_request",
            "change_request_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    change_request_id: Mapped[int] = mapped_column(
        ForeignKey("change_request.id", ondelete="CASCADE")
    )
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus), default=RunStatus.QUEUED)
    # Gap 3 from the spec review: a failed run restores this status instead of forcing DRAFT.
    cr_status_before: Mapped[ChangeRequestStatus] = mapped_column(enum_column(ChangeRequestStatus))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    llm_model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(60))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Written from its own short transaction while the run works, for the progress display.
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RunInput(Base):
    """The exact versions an analysis used: answers "what was this result based on?"."""

    __tablename__ = "run_input"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_run.id", ondelete="CASCADE"), index=True
    )
    input_type: Mapped[RunInputType] = mapped_column(enum_column(RunInputType))
    version_id: Mapped[int] = mapped_column(Integer)


class Finding(Base):
    """The lasting identity of a proposed impact, across every analysis that reproduces it.

    Gap 1 from the spec review: the specification gave Finding a single run_id, but a
    re-analysis must update the same finding while a finished run must never change. So
    what a given run saw lives in FindingOccurrence, and Finding keeps the current state.
    """

    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint"),
        Index("ix_finding_change_request_id", "change_request_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    change_request_id: Mapped[int] = mapped_column(
        ForeignKey("change_request.id", ondelete="CASCADE")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    req_key: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[TargetType] = mapped_column(enum_column(TargetType))
    target_key: Mapped[str] = mapped_column(Text)
    target_detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    impact_type: Mapped[ImpactType] = mapped_column(enum_column(ImpactType))
    link_type: Mapped[LinkType] = mapped_column(enum_column(LinkType))
    support: Mapped[Support] = mapped_column(enum_column(Support))
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[FindingStatus] = mapped_column(
        enum_column(FindingStatus), default=FindingStatus.PROPOSED
    )
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    # The reviewer's correction sits beside the original, which stays visible (FR-012).
    corrected_target: Mapped[str | None] = mapped_column(Text)
    corrected_explanation: Mapped[str | None] = mapped_column(Text)
    last_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    occurrences: Mapped[list["FindingOccurrence"]] = relationship(back_populates="finding")


class FindingOccurrence(Base):
    """What one run saw for one finding. Never modified after the run ends."""

    __tablename__ = "finding_occurrence"
    __table_args__ = (UniqueConstraint("finding_id", "run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("finding.id", ondelete="CASCADE"))
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_run.id", ondelete="CASCADE"), index=True
    )
    evidence_hash: Mapped[str] = mapped_column(String(64))
    explanation: Mapped[str] = mapped_column(Text)
    link_type: Mapped[LinkType] = mapped_column(enum_column(LinkType))
    support: Mapped[Support] = mapped_column(enum_column(Support))
    # The model's reading, kept apart from the rule-based explanation so a reviewer always
    # knows which sentence was computed and which was generated. Per run, so two models can
    # be compared on the same change (FR-020).
    llm_analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(200))
    llm_verdict: Mapped[LlmVerdict | None] = mapped_column(enum_column(LlmVerdict))
    llm_explanation: Mapped[str | None] = mapped_column(Text)
    llm_impact_type: Mapped[ImpactType | None] = mapped_column(enum_column(ImpactType))
    llm_uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list)

    finding: Mapped[Finding] = relationship(back_populates="occurrences")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="occurrence")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("finding_occurrence.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[EvidenceSource] = mapped_column(enum_column(EvidenceSource))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"))
    symbol_id: Mapped[int | None] = mapped_column(ForeignKey("code_symbol.id", ondelete="CASCADE"))
    requirement_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_version.id", ondelete="CASCADE")
    )
    quote: Mapped[str] = mapped_column(Text)
    role: Mapped[EvidenceRole] = mapped_column(enum_column(EvidenceRole))
    # A quote chosen by the LLM, then verified word for word. It stays out of evidence_hash:
    # a model rephrasing between runs must not reopen a decision a human already took.
    from_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    # Document location
    document_name: Mapped[str | None] = mapped_column(String(255))
    section_path: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    # Code location
    file_path: Mapped[str | None] = mapped_column(String(500))
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)

    occurrence: Mapped[FindingOccurrence] = relationship(back_populates="evidence")
    segment: Mapped[Segment | None] = relationship()
    symbol: Mapped[CodeSymbol | None] = relationship()
    requirement_version: Mapped[RequirementVersion | None] = relationship()


class TestSuggestion(Base):
    """Gap 2 from the spec review: tests come from the ChangeFact (pipeline step 9), so they
    belong to the run and the requirement, and must exist even when no finding does.
    finding_id stays optional for LLM scenarios tied to a specific finding later on.
    """

    __tablename__ = "test_suggestion"
    __test__ = False  # the name starts with "Test": tell pytest this is not a test class

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_run.id", ondelete="CASCADE"), index=True
    )
    req_key: Mapped[str] = mapped_column(String(60))
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("finding.id", ondelete="CASCADE"))
    change_fact: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_value: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    origin: Mapped[TestOrigin] = mapped_column(enum_column(TestOrigin))
    status: Mapped[TestStatus] = mapped_column(
        enum_column(TestStatus), default=TestStatus.SUGGESTED
    )
