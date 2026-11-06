from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent


def record_event(
    db: Session,
    action: str,
    *,
    project_id: int | None,
    actor_id: int | None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    **details: Any,
) -> None:
    """Adds the event to the caller's transaction, so an action and its audit record
    are committed together or not at all."""
    db.add(
        AuditEvent(
            project_id=project_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )
