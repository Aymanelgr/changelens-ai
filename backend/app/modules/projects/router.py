import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession, require_role
from app.core.enums import ProjectRole, RunStatus
from app.modules.analysis.models import AnalysisRun
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import record_event
from app.modules.auth.models import User
from app.modules.projects.models import Project, ProjectMember
from app.modules.requirements.models import ChangeRequest

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    my_role: ProjectRole | None = None


class MemberIn(BaseModel):
    email: str
    role: ProjectRole


class MemberOut(BaseModel):
    user_id: int
    email: str
    role: ProjectRole


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: DbSession, user: CurrentUser):
    project = Project(name=body.name, description=body.description)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.ADMIN))
    record_event(
        db,
        "PROJECT_CREATED",
        project_id=project.id,
        actor_id=user.id,
        entity_type="project",
        entity_id=project.id,
        name=project.name,
    )
    db.commit()
    out = ProjectOut.model_validate(project)
    out.my_role = ProjectRole.ADMIN
    return out


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: DbSession, user: CurrentUser):
    rows = db.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.id)
    ).all()
    result = []
    for project, role in rows:
        out = ProjectOut.model_validate(project)
        out.my_role = role
        result.append(out)
    return result


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: DbSession, user: CurrentUser):
    member = require_role(db, user, project_id)
    out = ProjectOut.model_validate(db.get(Project, project_id))
    out.my_role = member.role
    return out


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    rows = db.execute(
        select(ProjectMember, User.email)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.email)
    ).all()
    return [MemberOut(user_id=m.user_id, email=email, role=m.role) for m, email in rows]


@router.post("/projects/{project_id}/members", response_model=MemberOut)
def set_member(project_id: int, body: MemberIn, db: DbSession, user: CurrentUser):
    """Adds a member, or changes the role of an existing one (FR-017)."""
    require_role(db, user, project_id, ProjectRole.ADMIN)
    target = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur introuvable.")

    member = db.get(ProjectMember, (project_id, target.id))
    previous = member.role if member else None
    if member is None:
        member = ProjectMember(project_id=project_id, user_id=target.id, role=body.role)
        db.add(member)
    else:
        if previous == ProjectRole.ADMIN and body.role != ProjectRole.ADMIN:
            remaining = db.scalars(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.role == ProjectRole.ADMIN,
                    ProjectMember.user_id != target.id,
                )
            ).first()
            if remaining is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Le projet doit conserver au moins un administrateur."
                )
        member.role = body.role
    record_event(
        db,
        "ROLE_CHANGED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="user",
        entity_id=target.id,
        previous_role=previous,
        new_role=body.role,
    )
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, role=member.role)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, confirm: str, db: DbSession, user: CurrentUser):
    """FR-018: files, segments, vectors and findings all go. One minimal audit event stays: who,
    when, which project. The name must be retyped, because nothing can bring the data back."""
    require_role(db, user, project_id, ProjectRole.ADMIN)
    project = db.get(Project, project_id)
    if confirm != project.name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Le nom saisi ne correspond pas au projet."
        )
    active = db.scalar(
        select(AnalysisRun.id)
        .join(ChangeRequest, ChangeRequest.id == AnalysisRun.change_request_id)
        .where(
            ChangeRequest.project_id == project_id,
            AnalysisRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        )
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Une analyse est en cours sur ce projet.")

    name = project.name
    # Declares, for this transaction only, the single project whose history may be purged.
    # See migration 0002: every other delete on audit_event is still refused by the trigger.
    db.execute(
        text("SELECT set_config('changelens.audit_purge_project', :project, true)"),
        {"project": str(project_id)},
    )
    db.execute(delete(AuditEvent).where(AuditEvent.project_id == project_id))
    db.delete(project)  # foreign keys cascade to everything the project owns
    db.flush()
    record_event(
        db,
        "PROJECT_DELETED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="project",
        entity_id=project_id,
        name=name,
    )
    db.commit()
    # After the commit: a failed transaction must not leave a project without its files.
    shutil.rmtree(Path(get_settings().storage_dir) / str(project_id), ignore_errors=True)
