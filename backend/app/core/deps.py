"""Authentication and project-level authorization, shared by every router."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import ProjectRole
from app.core.security import decode_access_token
from app.modules.auth.models import User
from app.modules.projects.models import ProjectMember

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    user_id = decode_access_token(credentials.credentials) if credentials else None
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(db: Session, user: User, project_id: int, *allowed: ProjectRole) -> ProjectMember:
    """Every project-scoped endpoint goes through this check.

    A non-member gets 404, not 403: a 403 would confirm that the project exists.
    A member whose role is too low gets 403: they already know it exists.
    With no roles given, any member is allowed.
    """
    member = db.get(ProjectMember, (project_id, user.id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    if allowed and member.role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Rôle insuffisant pour cette action.")
    return member


CAN_IMPORT = (ProjectRole.ADMIN, ProjectRole.ANALYST)
CAN_ANALYZE = (ProjectRole.ADMIN, ProjectRole.ANALYST)
CAN_DECIDE = (ProjectRole.ADMIN, ProjectRole.REVIEWER)
