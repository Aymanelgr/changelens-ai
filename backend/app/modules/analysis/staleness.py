"""Marks findings whose evidence points at a version that is no longer the latest (section 4.4).
They are flagged, never deleted, until a new analysis refreshes them."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.enums import ChangeRequestStatus, FindingStatus
from app.modules.analysis.models import Evidence, Finding, FindingOccurrence
from app.modules.code_analysis.models import CodeSymbol
from app.modules.ingestion.models import DocumentVersion, Segment
from app.modules.requirements.models import ChangeRequest


def _base_query():
    return (
        select(Finding.id)
        .join(
            FindingOccurrence,
            (FindingOccurrence.finding_id == Finding.id)
            & (FindingOccurrence.run_id == Finding.last_run_id),
        )
        .join(Evidence, Evidence.occurrence_id == FindingOccurrence.id)
        .join(ChangeRequest, ChangeRequest.id == Finding.change_request_id)
        .where(
            Finding.status != FindingStatus.SUPERSEDED,
            ChangeRequest.status != ChangeRequestStatus.CLOSED,
        )
    )


def mark_stale_for_document(db: Session, document_id: int, new_version_id: int) -> int:
    query = (
        _base_query()
        .join(Segment, Segment.id == Evidence.segment_id)
        .join(DocumentVersion, DocumentVersion.id == Segment.document_version_id)
        .where(DocumentVersion.document_id == document_id, DocumentVersion.id != new_version_id)
    )
    return _flag(db, query)


def mark_stale_for_snapshot(db: Session, project_id: int, new_snapshot_id: int) -> int:
    query = (
        _base_query()
        .join(CodeSymbol, CodeSymbol.id == Evidence.symbol_id)
        .where(Finding.project_id == project_id, CodeSymbol.snapshot_id != new_snapshot_id)
    )
    return _flag(db, query)


def _flag(db: Session, query) -> int:
    finding_ids = db.scalars(query.distinct()).all()
    if finding_ids:
        db.execute(update(Finding).where(Finding.id.in_(finding_ids)).values(stale=True))
    return len(finding_ids)
