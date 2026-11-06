from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.core.enums import ImpactType, LinkType, ProjectRole, RunStatus, TargetType, TraceLinkStatus
from app.modules.analysis.models import Finding
from app.modules.analysis.router import get_run
from app.modules.audit.service import record_event
from app.modules.reporting.service import collect, render_html, render_markdown
from app.modules.review.models import TraceLink

router = APIRouter(tags=["reporting"])

CAN_EXPORT = (ProjectRole.ADMIN, ProjectRole.ANALYST, ProjectRole.REVIEWER)


@router.get("/analysis-runs/{run_id}/report")
def export_report(
    run_id: int,
    db: DbSession,
    user: CurrentUser,
    report_format: Literal["md", "html"] = Query(default="md", alias="format"),
    preview: bool = Query(default=False),
):
    """A preview reads the report on screen. Only a real export counts as an audited action."""
    run, change_request = get_run(db, run_id)
    require_role(db, user, change_request.project_id, *CAN_EXPORT)
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette analyse n'est pas terminée.")

    data = collect(db, run)
    if not preview:
        record_event(
            db,
            "REPORT_EXPORTED",
            project_id=change_request.project_id,
            actor_id=user.id,
            entity_type="analysis_run",
            entity_id=run.id,
            format=report_format,
            incomplete_review=data.open_count > 0,
        )
        db.commit()

    if report_format == "html":
        body, media_type = render_html(data), "text/html; charset=utf-8"
    else:
        body, media_type = render_markdown(data), "text/markdown; charset=utf-8"
    headers = (
        {}
        if preview
        else {"Content-Disposition": f'attachment; filename="rapport-{run.id}.{report_format}"'}
    )
    return Response(
        content=body,
        media_type=media_type,
        # The HTML holds imported text. Even though it is escaped, this page may run nothing.
        headers={
            **headers,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


class MapNode(BaseModel):
    id: str
    kind: Literal["requirement", "document", "code", "test"]
    label: str
    detail: str | None = None


class MapLink(BaseModel):
    source: str
    target: str
    status: TraceLinkStatus
    link_type: LinkType
    impact_type: ImpactType
    finding_id: int


class ImpactMapOut(BaseModel):
    nodes: list[MapNode]
    links: list[MapLink]


@router.get("/projects/{project_id}/impact-map", response_model=ImpactMapOut)
def impact_map(
    project_id: int,
    db: DbSession,
    user: CurrentUser,
    change_request_id: int | None = Query(default=None),
    req_key: str | None = Query(default=None),
):
    """Requirements linked to their documents, functions and tests (FR-013). A revoked link is
    a rejected proposal, so it is left off the map."""
    require_role(db, user, project_id)
    query = (
        select(TraceLink, Finding)
        .join(Finding, Finding.id == TraceLink.finding_id)
        .where(TraceLink.project_id == project_id, TraceLink.status != TraceLinkStatus.REVOKED)
        .order_by(TraceLink.req_key, TraceLink.target_key)
    )
    if change_request_id is not None:
        query = query.where(Finding.change_request_id == change_request_id)
    if req_key:
        query = query.where(TraceLink.req_key == req_key)

    nodes: dict[str, MapNode] = {}
    links: list[MapLink] = []
    for link, finding in db.execute(query):
        requirement_id = f"req:{link.req_key}"
        nodes.setdefault(
            requirement_id, MapNode(id=requirement_id, kind="requirement", label=link.req_key)
        )
        detail = finding.target_detail
        # A human correction wins: the map shows the target the reviewer settled on.
        if link.target_key != finding.target_key:
            kind, label, sub = "code", link.target_key, "cible corrigée"
        elif link.target_type == TargetType.CODE_SYMBOL:
            kind = "test" if detail.get("is_test") else "code"
            label, sub = detail.get("symbol", link.target_key), detail.get("file")
        else:
            kind = "document"
            label = detail.get("section") or detail.get("document") or link.target_key
            sub = detail.get("document")
        nodes.setdefault(
            link.target_key, MapNode(id=link.target_key, kind=kind, label=label, detail=sub)
        )
        links.append(
            MapLink(
                source=requirement_id,
                target=link.target_key,
                status=link.status,
                link_type=link.link_type,
                impact_type=link.impact_type,
                finding_id=finding.id,
            )
        )
    return ImpactMapOut(nodes=list(nodes.values()), links=links)
