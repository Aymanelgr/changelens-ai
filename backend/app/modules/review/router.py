from typing import Self

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import exists, select

from app.core.deps import CAN_DECIDE, CurrentUser, DbSession, require_role
from app.core.enums import (
    ChangeRequestStatus,
    Decision,
    FindingStatus,
    TraceLinkStatus,
)
from app.modules.analysis.models import Finding
from app.modules.audit.service import record_event
from app.modules.requirements.models import ChangeRequest
from app.modules.review.models import ReviewDecision, TraceLink

router = APIRouter(tags=["review"])


class DecisionIn(BaseModel):
    decision: Decision
    comment: str | None = None
    corrected_target: str | None = None
    corrected_explanation: str | None = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> Self:
        """Invalid combinations are refused with 422 before any business logic runs (FR-012)."""
        if self.decision == Decision.REJECT and not (self.comment or "").strip():
            raise ValueError("Un motif est obligatoire pour rejeter un finding.")
        if self.decision == Decision.CORRECT and not (
            (self.corrected_target or "").strip() or (self.corrected_explanation or "").strip()
        ):
            raise ValueError("Une correction demande une nouvelle cible ou explication.")
        return self


class TraceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TraceLinkStatus


class DecisionOut(BaseModel):
    finding_id: int
    status: FindingStatus
    change_request_status: ChangeRequestStatus
    trace_link: TraceLinkOut | None


class DecisionHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reviewer_id: int
    decision: Decision
    reason: str | None


_OUTCOMES = {
    Decision.ACCEPT: (FindingStatus.ACCEPTED, TraceLinkStatus.CONFIRMED),
    Decision.REJECT: (FindingStatus.REJECTED, TraceLinkStatus.REVOKED),
    Decision.CORRECT: (FindingStatus.CORRECTED, TraceLinkStatus.CONFIRMED),
}


@router.post(
    "/findings/{finding_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
)
def decide(finding_id: int, body: DecisionIn, db: DbSession, user: CurrentUser):
    # The row lock makes two reviewers deciding at once take turns: the second one gets 409.
    finding = db.scalar(select(Finding).where(Finding.id == finding_id).with_for_update())
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    require_role(db, user, finding.project_id, *CAN_DECIDE)

    change_request = db.get(ChangeRequest, finding.change_request_id)
    if change_request.status == ChangeRequestStatus.ANALYSIS_IN_PROGRESS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Une analyse est en cours sur cette demande.")
    if finding.status != FindingStatus.PROPOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ce finding a déjà été traité.")

    new_status, link_status = _OUTCOMES[body.decision]
    finding.status = new_status
    finding.evidence_changed = False

    correction = None
    if body.decision == Decision.CORRECT:
        # The original target and explanation stay in place, so both remain visible.
        finding.corrected_target = (body.corrected_target or "").strip() or None
        finding.corrected_explanation = (body.corrected_explanation or "").strip() or None
        correction = {
            "corrected_target": finding.corrected_target,
            "corrected_explanation": finding.corrected_explanation,
        }

    link = db.scalar(select(TraceLink).where(TraceLink.finding_id == finding.id))
    if link:
        link.status = link_status
        if finding.corrected_target:
            link.target_key = finding.corrected_target

    db.add(
        ReviewDecision(
            finding_id=finding.id,
            reviewer_id=user.id,
            decision=body.decision,
            reason=body.comment,
            correction=correction,
        )
    )
    record_event(
        db,
        "FINDING_DECIDED",
        project_id=finding.project_id,
        actor_id=user.id,
        entity_type="finding",
        entity_id=finding.id,
        decision=body.decision,
        comment=body.comment,
    )
    db.flush()

    has_open = db.scalar(
        select(
            exists().where(
                Finding.change_request_id == change_request.id,
                Finding.status == FindingStatus.PROPOSED,
            )
        )
    )
    if not has_open and change_request.status == ChangeRequestStatus.IN_REVIEW:
        change_request.status = ChangeRequestStatus.REVIEW_COMPLETED
    db.commit()

    return DecisionOut(
        finding_id=finding.id,
        status=finding.status,
        change_request_status=change_request.status,
        trace_link=TraceLinkOut.model_validate(link) if link else None,
    )


@router.get("/findings/{finding_id}/decisions", response_model=list[DecisionHistoryOut])
def list_decisions(finding_id: int, db: DbSession, user: CurrentUser):
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    require_role(db, user, finding.project_id)
    return db.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.finding_id == finding_id)
        .order_by(ReviewDecision.id)
    ).all()
