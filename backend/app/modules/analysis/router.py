from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import get_settings
from app.core.deps import CAN_ANALYZE, CurrentUser, DbSession, require_role
from app.core.enums import (
    ChangeRequestStatus,
    EvidenceRole,
    EvidenceSource,
    ExtractionStatus,
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
from app.llm.client import allowed_models, installed_ollama_models
from app.modules.analysis.models import (
    AnalysisRun,
    Evidence,
    Finding,
    FindingOccurrence,
    RunInput,
    TestSuggestion,
)
from app.modules.analysis.pipeline import run_analysis
from app.modules.audit.service import record_event
from app.modules.code_analysis.models import RepositorySnapshot
from app.modules.ingestion.models import Document, DocumentVersion
from app.modules.requirements.models import ChangeRequest
from app.modules.requirements.router import get_change_request

router = APIRouter(tags=["analysis"])

_CAN_START_FROM = {
    # Gap 4: the specification's diagram lacked the REVIEW_COMPLETED start allowed by FR-008.
    ChangeRequestStatus.DRAFT,
    ChangeRequestStatus.IN_REVIEW,
    ChangeRequestStatus.REVIEW_COMPLETED,
}


class RunCreate(BaseModel):
    llm_enabled: bool = True
    # A model from the allowed list, to compare two on the same change (FR-020).
    llm_model: str | None = None
    top_k: int = Field(default=0, ge=0, le=50)
    max_candidates_per_requirement: int = Field(default=0, ge=0, le=100)


class RunInputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    input_type: RunInputType
    version_id: int


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    change_request_id: int
    status: RunStatus
    params: dict[str, Any]
    llm_model: str | None
    prompt_version: str | None
    embedding_model: str | None
    summary: dict[str, Any]
    progress: dict[str, Any]
    failure_reason: str | None
    duration_ms: int | None
    total_tokens: int | None
    created_at: datetime
    finished_at: datetime | None
    inputs: list[RunInputOut] = []


class EvidenceOut(BaseModel):
    id: int
    source_type: EvidenceSource
    role: EvidenceRole
    quote: str
    document_name: str | None
    section_path: str | None
    page: int | None
    file_path: str | None
    start_line: int | None
    end_line: int | None
    from_llm: bool
    # The surrounding text, so the panel can show the quote highlighted in place.
    context: str
    context_start_line: int | None


class FindingOut(BaseModel):
    id: int
    req_key: str
    status: FindingStatus
    stale: bool
    evidence_changed: bool
    llm_analyzed: bool
    llm_model: str | None
    llm_verdict: LlmVerdict | None
    llm_explanation: str | None
    llm_impact_type: ImpactType | None
    llm_uncertainties: list[str]
    target_type: TargetType
    target_key: str
    target: dict[str, Any]
    impact_type: ImpactType
    link_type: LinkType
    support: Support
    explanation: str
    corrected_target: str | None
    corrected_explanation: str | None
    evidence: list[EvidenceOut]


class TestSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    req_key: str
    input_value: str
    expected: str
    rationale: str
    origin: TestOrigin
    status: TestStatus


def get_run(db: Session, run_id: int) -> tuple[AnalysisRun, ChangeRequest]:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
    return run, db.get(ChangeRequest, run.change_request_id)


def _run_out(db: Session, run: AnalysisRun) -> RunOut:
    out = RunOut.model_validate(run)
    inputs = db.scalars(select(RunInput).where(RunInput.run_id == run.id)).all()
    out.inputs = [RunInputOut.model_validate(item) for item in inputs]
    return out


def _latest_document_versions(db: Session, project_id: int) -> list[int]:
    latest = (
        select(DocumentVersion.document_id, func.max(DocumentVersion.number).label("number"))
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.project_id == project_id)
        .group_by(DocumentVersion.document_id)
        .subquery()
    )
    return list(
        db.scalars(
            select(DocumentVersion.id)
            .join(
                latest,
                (latest.c.document_id == DocumentVersion.document_id)
                & (latest.c.number == DocumentVersion.number),
            )
            .where(DocumentVersion.extraction_status == ExtractionStatus.EXTRACTED)
            .order_by(DocumentVersion.id)
        )
    )


@router.post(
    "/change-requests/{change_request_id}/analysis-runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_analysis(
    change_request_id: int,
    body: RunCreate,
    background: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
):
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id, *CAN_ANALYZE)
    if change_request.status == ChangeRequestStatus.ANALYSIS_IN_PROGRESS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Une analyse est déjà en cours.")
    if change_request.status not in _CAN_START_FROM:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette demande est close.")

    document_versions = _latest_document_versions(db, change_request.project_id)
    snapshot = db.scalar(
        select(RepositorySnapshot)
        .where(RepositorySnapshot.project_id == change_request.project_id)
        .order_by(RepositorySnapshot.id.desc())
    )
    if not document_versions and snapshot is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Importez au moins un document ou une archive de code avant de lancer l'analyse.",
        )

    settings = get_settings()
    if body.llm_model and body.llm_model not in allowed_models():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Modèle LLM non autorisé.")

    # NFR-005: everything needed to answer "how was this result produced?" is stored with the run.
    llm_on = body.llm_enabled and settings.llm_provider != "none"
    run = AnalysisRun(
        change_request_id=change_request.id,
        cr_status_before=change_request.status,
        params={
            "max_candidates_per_requirement": (
                body.max_candidates_per_requirement or settings.max_candidates_per_requirement
            ),
            "top_k": body.top_k or settings.search_top_k,
            "search_min_cosine": settings.search_min_cosine,
            "search_strong_cosine": settings.search_strong_cosine,
            "llm_enabled": llm_on,
            "llm_model": (body.llm_model or settings.llm_model) if llm_on else None,
            "llm_provider": settings.llm_provider if llm_on else None,
            "llm_temperature": settings.llm_temperature if llm_on else None,
            "llm_seed": settings.llm_seed if llm_on else None,
        },
        created_by=user.id,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        # Two simultaneous clicks: the partial unique index stopped the second one.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Une analyse est déjà en cours.") from exc

    db.add_all(
        [
            RunInput(
                run_id=run.id,
                input_type=RunInputType.REQUIREMENT_SET_BEFORE,
                version_id=change_request.set_before_id,
            ),
            RunInput(
                run_id=run.id,
                input_type=RunInputType.REQUIREMENT_SET_AFTER,
                version_id=change_request.set_after_id,
            ),
            *(
                RunInput(run_id=run.id, input_type=RunInputType.DOCUMENT_VERSION, version_id=v)
                for v in document_versions
            ),
        ]
    )
    if snapshot:
        db.add(
            RunInput(
                run_id=run.id, input_type=RunInputType.REPOSITORY_SNAPSHOT, version_id=snapshot.id
            )
        )
    change_request.status = ChangeRequestStatus.ANALYSIS_IN_PROGRESS
    record_event(
        db,
        "ANALYSIS_STARTED",
        project_id=change_request.project_id,
        actor_id=user.id,
        entity_type="analysis_run",
        entity_id=run.id,
    )
    db.commit()
    background.add_task(run_analysis, run.id)
    return _run_out(db, run)


@router.get("/change-requests/{change_request_id}/analysis-runs", response_model=list[RunOut])
def list_runs(change_request_id: int, db: DbSession, user: CurrentUser):
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id)
    runs = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.change_request_id == change_request_id)
        .order_by(AnalysisRun.id.desc())
    ).all()
    return [_run_out(db, run) for run in runs]


@router.get("/analysis-runs/{run_id}", response_model=RunOut)
def read_run(run_id: int, db: DbSession, user: CurrentUser):
    run, change_request = get_run(db, run_id)
    require_role(db, user, change_request.project_id)
    return _run_out(db, run)


def _evidence_out(evidence: Evidence) -> EvidenceOut:
    context = ""
    context_start = None
    if evidence.segment is not None:
        context = evidence.segment.text
    elif evidence.symbol is not None:
        context = evidence.symbol.source_text
        context_start = evidence.symbol.start_line
    elif evidence.requirement_version is not None:
        context = evidence.requirement_version.text
    return EvidenceOut(
        id=evidence.id,
        source_type=evidence.source_type,
        role=evidence.role,
        quote=evidence.quote,
        document_name=evidence.document_name,
        section_path=evidence.section_path,
        page=evidence.page,
        file_path=evidence.file_path,
        start_line=evidence.start_line,
        end_line=evidence.end_line,
        from_llm=evidence.from_llm,
        context=context,
        context_start_line=context_start,
    )


@router.get("/analysis-runs/{run_id}/findings", response_model=list[FindingOut])
def list_findings(
    run_id: int,
    db: DbSession,
    user: CurrentUser,
    link_type: LinkType | None = Query(default=None),
    finding_status: FindingStatus | None = Query(default=None, alias="status"),
    req_key: str | None = Query(default=None),
):
    run, change_request = get_run(db, run_id)
    require_role(db, user, change_request.project_id)

    query = (
        select(FindingOccurrence)
        .join(Finding, Finding.id == FindingOccurrence.finding_id)
        .options(
            joinedload(FindingOccurrence.finding),
            selectinload(FindingOccurrence.evidence).options(
                joinedload(Evidence.segment),
                joinedload(Evidence.symbol),
                joinedload(Evidence.requirement_version),
            ),
        )
        .where(FindingOccurrence.run_id == run.id)
        .order_by(
            Finding.req_key,
            # Contradictions go to review first (section 5.8).
            case((Finding.impact_type == ImpactType.INCONSISTENCY, 0), else_=1),
            Finding.target_key,
        )
    )
    if link_type:
        query = query.where(Finding.link_type == link_type)
    if finding_status:
        query = query.where(Finding.status == finding_status)
    if req_key:
        query = query.where(Finding.req_key == req_key)

    results = []
    for occurrence in db.scalars(query).all():
        finding = occurrence.finding
        results.append(
            FindingOut(
                id=finding.id,
                req_key=finding.req_key,
                status=finding.status,
                stale=finding.stale,
                evidence_changed=finding.evidence_changed,
                # What the model said in this run, not in the latest one.
                llm_analyzed=occurrence.llm_analyzed,
                llm_model=occurrence.llm_model,
                llm_verdict=occurrence.llm_verdict,
                llm_explanation=occurrence.llm_explanation,
                llm_impact_type=occurrence.llm_impact_type,
                llm_uncertainties=occurrence.llm_uncertainties,
                target_type=finding.target_type,
                target_key=finding.target_key,
                target=finding.target_detail,
                impact_type=finding.impact_type,
                # What this run saw comes from the occurrence, so history stays accurate.
                link_type=occurrence.link_type,
                support=occurrence.support,
                explanation=occurrence.explanation,
                corrected_target=finding.corrected_target,
                corrected_explanation=finding.corrected_explanation,
                evidence=[_evidence_out(e) for e in occurrence.evidence],
            )
        )
    return results


@router.get("/analysis-runs/{run_id}/test-suggestions", response_model=list[TestSuggestionOut])
def list_test_suggestions(run_id: int, db: DbSession, user: CurrentUser):
    run, change_request = get_run(db, run_id)
    require_role(db, user, change_request.project_id)
    return db.scalars(
        select(TestSuggestion).where(TestSuggestion.run_id == run.id).order_by(TestSuggestion.id)
    ).all()


class ComparisonRow(BaseModel):
    finding_id: int
    req_key: str
    target_key: str
    impact_type: ImpactType
    a_verdict: LlmVerdict | None
    b_verdict: LlmVerdict | None
    a_explanation: str | None
    b_explanation: str | None


class ComparisonOut(BaseModel):
    run_a: int
    run_b: int
    model_a: str | None
    model_b: str | None
    duration_a_ms: int | None
    duration_b_ms: int | None
    tokens_a: int | None
    tokens_b: int | None
    only_in_a: int
    only_in_b: int
    agreements: int
    disagreements: int
    rows: list[ComparisonRow]


@router.get("/change-requests/{change_request_id}/compare-runs", response_model=ComparisonOut)
def compare_runs(change_request_id: int, a: int, b: int, db: DbSession, user: CurrentUser):
    """FR-020: what two runs, typically two LLM models, concluded on the same change."""
    change_request = get_change_request(db, change_request_id)
    require_role(db, user, change_request.project_id)
    runs = {}
    for run_id in (a, b):
        run = db.get(AnalysisRun, run_id)
        if run is None or run.change_request_id != change_request_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ressource introuvable.")
        runs[run_id] = run

    occurrences = db.execute(
        select(FindingOccurrence, Finding)
        .join(Finding, Finding.id == FindingOccurrence.finding_id)
        .where(FindingOccurrence.run_id.in_([a, b]))
        .order_by(Finding.req_key, Finding.target_key)
    ).all()
    by_finding: dict[int, dict[int, FindingOccurrence]] = {}
    findings: dict[int, Finding] = {}
    for occurrence, finding in occurrences:
        by_finding.setdefault(finding.id, {})[occurrence.run_id] = occurrence
        findings[finding.id] = finding

    rows = []
    agreements = disagreements = only_a = only_b = 0
    for finding_id, seen in by_finding.items():
        left, right = seen.get(a), seen.get(b)
        only_a += right is None
        only_b += left is None
        if left is not None and right is not None:
            if left.llm_verdict == right.llm_verdict:
                agreements += 1
            else:
                disagreements += 1
        finding = findings[finding_id]
        rows.append(
            ComparisonRow(
                finding_id=finding_id,
                req_key=finding.req_key,
                target_key=finding.target_key,
                impact_type=finding.impact_type,
                a_verdict=left.llm_verdict if left else None,
                b_verdict=right.llm_verdict if right else None,
                a_explanation=left.llm_explanation if left else None,
                b_explanation=right.llm_explanation if right else None,
            )
        )
    return ComparisonOut(
        run_a=a,
        run_b=b,
        model_a=runs[a].llm_model,
        model_b=runs[b].llm_model,
        duration_a_ms=runs[a].duration_ms,
        duration_b_ms=runs[b].duration_ms,
        tokens_a=runs[a].total_tokens,
        tokens_b=runs[b].total_tokens,
        only_in_a=only_a,
        only_in_b=only_b,
        agreements=agreements,
        disagreements=disagreements,
        rows=rows,
    )


class LlmStatusOut(BaseModel):
    provider: str
    default_model: str
    allowed_models: list[str]
    reachable: bool
    installed_models: list[str]
    embedding_provider: str
    embedding_model: str


@router.get("/system/llm-status", response_model=LlmStatusOut)
def llm_status(user: CurrentUser):
    """Tells the analysis screen what will really happen before anyone clicks."""
    settings = get_settings()
    installed = installed_ollama_models() if settings.llm_provider == "ollama" else []
    reachable = settings.llm_provider == "remote" or installed is not None
    return LlmStatusOut(
        provider=settings.llm_provider,
        default_model=settings.llm_model,
        allowed_models=allowed_models(),
        reachable=settings.llm_provider != "none" and reachable,
        installed_models=installed or [],
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
    )
