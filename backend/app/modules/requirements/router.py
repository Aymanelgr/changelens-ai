import hashlib
from datetime import datetime
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import CAN_ANALYZE, CAN_IMPORT, CurrentUser, DbSession, require_role
from app.core.enums import ChangeRequestStatus
from app.modules.audit.service import record_event
from app.modules.requirements.diffing import diff_requirements
from app.modules.requirements.models import ChangeRequest, RequirementSet, RequirementVersion
from app.modules.requirements.parser import (
    RequirementFileError,
    parse_requirements,
    text_hash,
)

router = APIRouter(tags=["requirements"])


class RequirementSetOut(BaseModel):
    id: int
    number: int
    source_name: str
    requirement_count: int
    created_at: datetime
    already_imported: bool = False


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    req_key: str
    title: str
    text: str


class ChangeRequestCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    set_before_id: int
    set_after_id: int


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    set_before_id: int
    set_after_id: int
    status: ChangeRequestStatus
    created_at: datetime


class RequirementChangeOut(BaseModel):
    req_key: str
    status: str
    title: str
    old_text: str | None
    new_text: str | None
    facts: list[dict[str, Any]]
    word_diff: list[dict[str, str]]


def load_set(db, set_id: int) -> list[RequirementVersion]:
    return list(
        db.scalars(
            select(RequirementVersion)
            .where(RequirementVersion.requirement_set_id == set_id)
            .order_by(RequirementVersion.id)
        )
    )


def get_change_request(db, change_request_id: int) -> ChangeRequest:
    change_request = db.get(ChangeRequest, change_request_id)
    if change_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    return change_request


@router.post("/projects/{project_id}/requirement-sets", response_model=RequirementSetOut)
def upload_requirements(project_id: int, file: UploadFile, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id, *CAN_IMPORT)
    limit = get_settings().max_document_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Fichier trop volumineux.")
    name = PurePath((file.filename or "").replace("\\", "/")).name
    try:
        parsed = parse_requirements(name, data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Le fichier doit être encodé en UTF-8."
        ) from exc
    except RequirementFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(RequirementSet).where(
            RequirementSet.project_id == project_id, RequirementSet.sha256 == digest
        )
    )
    if existing:
        count = db.scalar(
            select(func.count())
            .select_from(RequirementVersion)
            .where(RequirementVersion.requirement_set_id == existing.id)
        )
        return RequirementSetOut(
            id=existing.id,
            number=existing.number,
            source_name=existing.source_name,
            requirement_count=count,
            created_at=existing.created_at,
            already_imported=True,
        )

    last_number = db.scalar(
        select(func.max(RequirementSet.number)).where(RequirementSet.project_id == project_id)
    )
    requirement_set = RequirementSet(
        project_id=project_id, number=(last_number or 0) + 1, sha256=digest, source_name=name[:255]
    )
    db.add(requirement_set)
    db.flush()
    db.add_all(
        RequirementVersion(
            requirement_set_id=requirement_set.id,
            req_key=item.key,
            title=item.title,
            text=item.text,
            text_hash=text_hash(item.text),
        )
        for item in parsed
    )
    record_event(
        db,
        "REQUIREMENTS_UPLOADED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="requirement_set",
        entity_id=requirement_set.id,
        number=requirement_set.number,
        count=len(parsed),
    )
    db.commit()
    return RequirementSetOut(
        id=requirement_set.id,
        number=requirement_set.number,
        source_name=requirement_set.source_name,
        requirement_count=len(parsed),
        created_at=requirement_set.created_at,
    )


@router.get("/projects/{project_id}/requirement-sets", response_model=list[RequirementSetOut])
def list_requirement_sets(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    rows = db.execute(
        select(RequirementSet, func.count(RequirementVersion.id))
        .outerjoin(RequirementVersion)
        .where(RequirementSet.project_id == project_id)
        .group_by(RequirementSet.id)
        .order_by(RequirementSet.number)
    ).all()
    return [
        RequirementSetOut(
            id=item.id,
            number=item.number,
            source_name=item.source_name,
            requirement_count=count,
            created_at=item.created_at,
        )
        for item, count in rows
    ]


@router.get("/requirement-sets/{set_id}/requirements", response_model=list[RequirementOut])
def list_requirements(set_id: int, db: DbSession, user: CurrentUser):
    requirement_set = db.get(RequirementSet, set_id)
    if requirement_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    require_role(db, user, requirement_set.project_id)
    return load_set(db, set_id)


@router.post(
    "/projects/{project_id}/change-requests",
    response_model=ChangeRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def create_change_request(
    project_id: int, body: ChangeRequestCreate, db: DbSession, user: CurrentUser
):
    require_role(db, user, project_id, *CAN_ANALYZE)
    if body.set_before_id == body.set_after_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Choisissez deux versions différentes."
        )
    for set_id in (body.set_before_id, body.set_after_id):
        requirement_set = db.get(RequirementSet, set_id)
        # A set from another project must look exactly like a set that does not exist.
        if requirement_set is None or requirement_set.project_id != project_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Version d'exigences inconnue : {set_id}."
            )
    change_request = ChangeRequest(
        project_id=project_id,
        title=body.title,
        set_before_id=body.set_before_id,
        set_after_id=body.set_after_id,
        author_id=user.id,
    )
    db.add(change_request)
    db.flush()
    record_event(
        db,
        "CHANGE_REQUEST_CREATED",
        project_id=project_id,
        actor_id=user.id,
        entity_type="change_request",
        entity_id=change_request.id,
        title=body.title,
    )
    db.commit()
    return change_request


@router.get("/projects/{project_id}/change-requests", response_model=list[ChangeRequestOut])
def list_change_requests(project_id: int, db: DbSession, user: CurrentUser):
    require_role(db, user, project_id)
    return db.scalars(
        select(ChangeRequest)
        .where(ChangeRequest.project_id == project_id)
        .order_by(ChangeRequest.id.desc())
    ).all()


@router.get("/change-requests/{change_request_id}", response_model=ChangeRequestOut)
def read_change_request(change_request_id: int, db: DbSession, user: CurrentUser):
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id)
    return change_request


@router.get("/change-requests/{change_request_id}/diff", response_model=list[RequirementChangeOut])
def read_diff(change_request_id: int, db: DbSession, user: CurrentUser):
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id)
    changes = diff_requirements(
        load_set(db, change_request.set_before_id), load_set(db, change_request.set_after_id)
    )
    return [RequirementChangeOut(**vars(change)) for change in changes]


@router.post("/change-requests/{change_request_id}/close", response_model=ChangeRequestOut)
def close_change_request(change_request_id: int, db: DbSession, user: CurrentUser):
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id, *CAN_ANALYZE)
    if change_request.status != ChangeRequestStatus.REVIEW_COMPLETED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Seule une demande dont la revue est terminée peut être close.",
        )
    change_request.status = ChangeRequestStatus.CLOSED
    record_event(
        db,
        "CHANGE_REQUEST_CLOSED",
        project_id=change_request.project_id,
        actor_id=user.id,
        entity_type="change_request",
        entity_id=change_request.id,
    )
    db.commit()
    return change_request
