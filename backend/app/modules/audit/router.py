from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User

# FR-016: this router deliberately exposes GET only. No update or delete route exists.
router = APIRouter(tags=["audit"])


class AuditEventOut(BaseModel):
    id: int
    actor_id: int | None
    actor_email: str | None
    action: str
    entity_type: str | None
    entity_id: int | None
    details: dict[str, Any]
    created_at: datetime


def _rows(db, query) -> list[AuditEventOut]:
    return [
        AuditEventOut(
            id=event.id,
            actor_id=event.actor_id,
            actor_email=email,
            action=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            details=event.details,
            created_at=event.created_at,
        )
        for event, email in db.execute(query)
    ]


def _events():
    return select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.actor_id)


@router.get("/projects/{project_id}/audit-events", response_model=list[AuditEventOut])
def list_audit_events(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    return _rows(db, _events().where(AuditEvent.project_id == project_id).order_by(AuditEvent.id))


@router.get("/audit-events/project-deletions", response_model=list[AuditEventOut])
def list_project_deletions(db: DbSession, user: CurrentUser):
    """The trace FR-018 keeps. The project is gone, so no project member can reach it any more:
    it is visible to global administrators only."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Rôle insuffisant pour cette action.")
    return _rows(
        db, _events().where(AuditEvent.action == "PROJECT_DELETED").order_by(AuditEvent.id)
    )
