"""Runs one analysis from start to finish in a background task (pipeline steps 3, 5 to 10)."""

import hashlib
import logging
import time
from collections import Counter
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.core.enums import (
    ChangeRequestStatus,
    FindingStatus,
    LinkType,
    RunInputType,
    RunStatus,
    Support,
    TestOrigin,
    TraceLinkStatus,
)
from app.llm.analysis import PROMPT_VERSION
from app.llm.client import LLMClient, LlmUnavailable, get_llm_client
from app.modules.analysis.candidates import (
    Candidate,
    abstention_candidate,
    cap_candidates,
    deterministic_candidates,
    is_vague_change,
    search_candidates,
)
from app.modules.analysis.llm_step import LlmStats, apply_llm
from app.modules.analysis.models import (
    AnalysisRun,
    Evidence,
    Finding,
    FindingOccurrence,
    RunInput,
    TestSuggestion,
)
from app.modules.audit.service import record_event
from app.modules.code_analysis.models import CallEdge, CodeSymbol, RepositorySnapshot
from app.modules.indexing.embeddings import Embedder, EmbeddingUnavailable, get_embedder
from app.modules.indexing.search import SearchHit, SearchMode, search
from app.modules.indexing.service import ensure_indexed
from app.modules.ingestion.models import Document, DocumentVersion, Segment
from app.modules.requirements.boundary_tests import suggest_boundary_tests
from app.modules.requirements.diffing import RequirementChange, diff_requirements
from app.modules.requirements.facts import has_verifiable_fact
from app.modules.requirements.models import ChangeRequest, RequirementVersion
from app.modules.review.models import TraceLink

logger = logging.getLogger(__name__)

_DECIDED = {FindingStatus.ACCEPTED, FindingStatus.REJECTED, FindingStatus.CORRECTED}


def fingerprint(project_id: int, change_request_id: int, candidate: Candidate) -> str:
    """Identifies "the same finding" across runs. It uses stable targets, not line numbers.

    The change request is part of it, which the specification left out: without it, a later
    change to the same requirement would inherit decisions made about an earlier change.
    """
    parts = [
        project_id,
        change_request_id,
        candidate.req_key,
        candidate.target_type,
        candidate.target_key,
        candidate.impact_type,
    ]
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def evidence_hash(candidate: Candidate) -> str:
    """Line numbers are excluded: a function moving down a file is not new evidence."""
    # Quotes chosen by the LLM are excluded too: a model rewording its answer is not new evidence.
    items = sorted(
        (e.file_path or e.document_name or "", e.section_path or "", e.quote, str(e.role))
        for e in candidate.evidence
        if not e.from_llm
    )
    return hashlib.sha256(repr(items).encode()).hexdigest()


def _persist_candidate(
    db: Session, run: AnalysisRun, change_request: ChangeRequest, candidate: Candidate
) -> Finding:
    identity = fingerprint(change_request.project_id, change_request.id, candidate)
    new_hash = evidence_hash(candidate)
    finding = db.scalar(
        select(Finding).where(
            Finding.project_id == change_request.project_id, Finding.fingerprint == identity
        )
    )

    if finding is None:
        finding = Finding(
            project_id=change_request.project_id,
            change_request_id=change_request.id,
            fingerprint=identity,
            req_key=candidate.req_key,
            target_type=candidate.target_type,
            target_key=candidate.target_key,
            target_detail=candidate.target_detail,
            impact_type=candidate.impact_type,
            link_type=candidate.link_type,
            support=candidate.support,
            explanation=candidate.explanation,
            llm_analyzed=candidate.llm_analyzed,
            last_run_id=run.id,
        )
        db.add(finding)
        db.flush()
        # An abstention links the requirement to nothing, so it creates no trace link.
        if candidate.link_type != LinkType.INSUFFICIENT_EVIDENCE:
            db.add(
                TraceLink(
                    project_id=finding.project_id,
                    finding_id=finding.id,
                    req_key=finding.req_key,
                    target_type=finding.target_type,
                    target_key=finding.target_key,
                    impact_type=finding.impact_type,
                    link_type=finding.link_type,
                    status=TraceLinkStatus.SUGGESTED,
                )
            )
    else:
        _refresh_existing(db, run, finding, candidate, new_hash)

    occurrence = FindingOccurrence(
        finding_id=finding.id,
        run_id=run.id,
        evidence_hash=new_hash,
        explanation=candidate.explanation,
        link_type=candidate.link_type,
        support=candidate.support,
        llm_analyzed=candidate.llm_analyzed,
        llm_model=candidate.llm_model,
        llm_verdict=candidate.llm_verdict,
        llm_explanation=candidate.llm_explanation,
        llm_impact_type=candidate.llm_impact_type,
        llm_uncertainties=candidate.llm_uncertainties,
    )
    db.add(occurrence)
    db.flush()
    db.add_all(
        Evidence(
            occurrence_id=occurrence.id,
            source_type=item.source_type,
            segment_id=item.segment_id,
            symbol_id=item.symbol_id,
            requirement_version_id=item.requirement_version_id,
            quote=item.quote,
            role=item.role,
            from_llm=item.from_llm,
            document_name=item.document_name,
            section_path=item.section_path,
            page=item.page,
            file_path=item.file_path,
            start_line=item.start_line,
            end_line=item.end_line,
        )
        for item in candidate.evidence
    )
    return finding


def _refresh_existing(
    db: Session, run: AnalysisRun, finding: Finding, candidate: Candidate, new_hash: str
) -> None:
    previous = db.scalar(
        select(FindingOccurrence).where(
            FindingOccurrence.finding_id == finding.id,
            FindingOccurrence.run_id == finding.last_run_id,
        )
    )
    changed = previous is not None and previous.evidence_hash != new_hash
    reopen = (finding.status in _DECIDED and changed) or finding.status == FindingStatus.SUPERSEDED

    finding.target_detail = candidate.target_detail
    finding.link_type = candidate.link_type
    finding.support = candidate.support
    finding.explanation = candidate.explanation
    finding.llm_analyzed = candidate.llm_analyzed
    finding.last_run_id = run.id
    finding.stale = False
    # Stays set until a reviewer decides: a later identical run must not hide the change.
    finding.evidence_changed = finding.evidence_changed or changed

    if reopen:
        was = finding.status
        finding.status = FindingStatus.PROPOSED
        link = db.scalar(select(TraceLink).where(TraceLink.finding_id == finding.id))
        if link:
            link.status = TraceLinkStatus.SUGGESTED
        record_event(
            db,
            "FINDING_REOPENED",
            project_id=finding.project_id,
            actor_id=None,  # done by the system, as the specification requires
            entity_type="finding",
            entity_id=finding.id,
            previous_status=was,
            reason="preuves modifiées" if changed else "de nouveau produit par l'analyse",
        )


def _supersede_missing(db: Session, run: AnalysisRun, change_request_id: int) -> int:
    """A proposal the latest run no longer produces is withdrawn. Decisions already taken stay."""
    missing = db.scalars(
        select(Finding).where(
            Finding.change_request_id == change_request_id,
            Finding.status == FindingStatus.PROPOSED,
            Finding.last_run_id != run.id,
        )
    ).all()
    for finding in missing:
        finding.status = FindingStatus.SUPERSEDED
        finding.stale = False
        link = db.scalar(select(TraceLink).where(TraceLink.finding_id == finding.id))
        if link:
            link.status = TraceLinkStatus.REVOKED
    return len(missing)


def _report_progress(run_id: int, step: str, done: int | None = None, total: int | None = None):
    """In its own transaction, so the browser can see it while the run's work is uncommitted.
    The run's own session must not touch this row between two commits, or this would wait."""
    with SessionLocal() as session:
        session.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .values(progress={"step": step, "done": done, "total": total})
        )
        session.commit()


def _prepare_search(
    db: Session,
    run: AnalysisRun,
    project_id: int,
    version_ids: list[int],
    snapshot_ids: list[int],
) -> tuple[Embedder | None, dict[str, Any], list[str]]:
    """Step 5. A missing model does not stop the analysis: search falls back to keywords."""
    embedder = get_embedder()
    if embedder is None:
        return None, {"mode": "KEYWORD_ONLY", "embedded": 0, "reused": 0}, []
    _report_progress(run.id, "INDEXING")
    try:
        stats = ensure_indexed(
            db,
            embedder,
            project_id,
            version_ids,
            snapshot_ids,
            lambda done, total: _report_progress(run.id, "INDEXING", done, total),
        )
    except EmbeddingUnavailable as exc:
        logger.warning("Embeddings unavailable: %s", exc)
        db.rollback()
        run.embedding_model = None
        db.commit()
        return (
            None,
            {"mode": "KEYWORD_ONLY", "embedded": 0, "reused": 0},
            ["Modèle d'embeddings indisponible : recherche par mots-clés uniquement."],
        )
    run.embedding_model = embedder.model_name
    # Committed now: vectors stay useful even if a later step fails.
    db.commit()
    return embedder, {"mode": "HYBRID", **stats}, []


def _search_change(
    db: Session,
    change: RequirementChange,
    embedder: Embedder | None,
    version_ids: list[int],
    snapshot_ids: list[int],
    top_k: int,
) -> list[SearchHit]:
    query = " ".join(filter(None, [change.title, change.old_text, change.new_text]))
    vector = None
    if embedder is not None:
        vector = embedder.embed_query(query)
    return search(
        db,
        query,
        vector,
        embedder.model_name if embedder else None,
        version_ids,
        snapshot_ids,
        top_k,
        SearchMode.HYBRID,
    )


def _abstention_reason(
    change: RequirementChange, deterministic: list[Candidate], inferred: list[Candidate]
) -> str | None:
    """Rules 1 and 4 of section 5.7, checked before any model is asked anything."""
    if deterministic:
        return None
    if is_vague_change(change):
        return "FORMULATION_VAGUE"
    if not inferred:
        return "RECHERCHE_FAIBLE"
    if not has_verifiable_fact(change.facts) and all(c.support == Support.WEAK for c in inferred):
        return "AUCUN_FAIT_NI_CANDIDAT_MODERE"
    return None


def _llm_summary(
    requested: bool, client: LLMClient | None, stats: LlmStats, attempted: bool
) -> dict[str, Any]:
    settings = get_settings()
    if not requested or client is None:
        status, label = "DISABLED", "non analysé par le LLM"
    elif stats.unavailable_error:
        status, label = "UNAVAILABLE", "LLM indisponible : mode dégradé, non analysé par le LLM"
    elif not attempted:
        status, label = "NOT_NEEDED", "aucun candidat à soumettre au LLM"
    else:
        status, label = "ANALYZED", f"analysé par {client.model}"
    cost = None
    if client is not None and not client.is_local:
        cost = (
            stats.input_tokens * settings.remote_input_cost_per_mtok
            + stats.output_tokens * settings.remote_output_cost_per_mtok
        ) / 1_000_000
    return {
        "status": status,
        "label": label,
        "where": None if client is None else ("local" if client.is_local else "remote"),
        "model": client.model if client else None,
        "analyzed": stats.analyzed,
        "discarded": stats.discarded,
        "retries": stats.retries,
        "validation_failures": stats.validation_failures,
        "rejected_outputs": stats.rejected,
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "estimated_cost": cost,
    }


def _execute(db: Session, run: AnalysisRun) -> None:
    started = time.perf_counter()
    run_id = run.id
    change_request = db.get(ChangeRequest, run.change_request_id)
    inputs = db.scalars(select(RunInput).where(RunInput.run_id == run.id)).all()
    version_ids = {
        kind: [i.version_id for i in inputs if i.input_type == kind] for kind in RunInputType
    }

    before = db.scalars(
        select(RequirementVersion).where(
            RequirementVersion.requirement_set_id == change_request.set_before_id
        )
    ).all()
    after = db.scalars(
        select(RequirementVersion).where(
            RequirementVersion.requirement_set_id == change_request.set_after_id
        )
    ).all()
    after_by_key = {r.req_key: r for r in after}
    before_by_key = {r.req_key: r for r in before}
    changes = diff_requirements(before, after)

    segments = db.execute(
        select(Segment, Document.name)
        .join(DocumentVersion, DocumentVersion.id == Segment.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Segment.document_version_id.in_(version_ids[RunInputType.DOCUMENT_VERSION]))
        .order_by(Segment.id)
    ).all()
    snapshot_ids = version_ids[RunInputType.REPOSITORY_SNAPSHOT]
    symbols = db.scalars(
        select(CodeSymbol).where(CodeSymbol.snapshot_id.in_(snapshot_ids)).order_by(CodeSymbol.id)
    ).all()
    edges = db.scalars(select(CallEdge).where(CallEdge.snapshot_id.in_(snapshot_ids))).all()

    settings = get_settings()
    max_candidates = run.params.get(
        "max_candidates_per_requirement", settings.max_candidates_per_requirement
    )
    top_k = run.params.get("top_k", settings.search_top_k)
    llm_requested = run.params.get("llm_enabled", True)
    client = get_llm_client(run.params.get("llm_model")) if llm_requested else None
    llm_stats = LlmStats()
    llm_attempted = False

    document_ids = version_ids[RunInputType.DOCUMENT_VERSION]
    embedder, search_summary, warnings = _prepare_search(
        db, run, change_request.project_id, document_ids, snapshot_ids
    )

    impacts: Counter[str] = Counter()
    links: Counter[str] = Counter()
    abstention_reasons: Counter[str] = Counter()
    capped = rejected_evidence = test_count = inferred_count = 0

    to_analyze = [change for change in changes if change.status in {"MODIFIED", "REMOVED"}]
    for position, change in enumerate(to_analyze, start=1):
        _report_progress(run_id, "ANALYZING", position, len(to_analyze))
        old_version = before_by_key.get(change.req_key)
        new_version = after_by_key.get(change.req_key)
        requirement = new_version or old_version
        deterministic = deterministic_candidates(
            change, segments, symbols, edges, old_version, new_version
        )
        taken = {candidate.target_key for candidate in deterministic}
        hits = _search_change(db, change, embedder, document_ids, snapshot_ids, top_k)
        inferred = search_candidates(
            change, hits, taken, settings.search_min_cosine, settings.search_strong_cosine
        )

        reason = None
        if change.status == "MODIFIED":
            reason = _abstention_reason(change, deterministic, inferred)
        # A change abstained on in advance sends nothing to the search step or to the model.
        candidates, dropped = cap_candidates(
            deterministic if reason else [*deterministic, *inferred], max_candidates
        )
        capped += dropped

        # NFR-004: a citation that is not literally in its source never reaches a reviewer.
        for candidate in candidates:
            verified = [e for e in candidate.evidence if e.is_verified]
            rejected_evidence += len(candidate.evidence) - len(verified)
            candidate.evidence = verified
        candidates = [c for c in candidates if c.evidence]

        if client is not None and candidates and not llm_stats.unavailable_error:
            llm_attempted = True
            try:
                candidates = apply_llm(client, change, candidates, llm_stats)
            except LlmUnavailable as exc:
                # FR-019: stop asking, and finish with what rules and search already found.
                logger.warning("LLM unavailable, degraded mode: %s", exc)
                llm_stats.unavailable_error = str(exc)
                warnings.append(f"LLM indisponible, mode dégradé : {exc}"[:300])
            else:
                if not candidates and change.status == "MODIFIED":
                    reason = "LLM_SANS_CONCLUSION"  # rules 2 and 3 of section 5.7

        # A quote the LLM added goes through the same check as every other one.
        for candidate in candidates:
            verified = [e for e in candidate.evidence if e.is_verified]
            rejected_evidence += len(candidate.evidence) - len(verified)
            candidate.evidence = verified

        if not candidates and change.status == "MODIFIED":
            candidates = [abstention_candidate(change, requirement)]
            abstention_reasons[reason or "AUCUN_CANDIDAT"] += 1

        inferred_count += sum(1 for candidate in candidates if candidate.from_search)
        for candidate in candidates:
            _persist_candidate(db, run, change_request, candidate)
            impacts[candidate.impact_type] += 1
            links[candidate.link_type] += 1

        for fact in change.facts:
            for test in suggest_boundary_tests(fact, change.new_text or ""):
                db.add(
                    TestSuggestion(
                        run_id=run.id,
                        req_key=change.req_key,
                        change_fact=fact,
                        input_value=test["input"],
                        expected=test["expected"],
                        rationale=test["rationale"],
                        origin=TestOrigin.RULE,
                    )
                )
                test_count += 1
    db.flush()

    superseded = _supersede_missing(db, run, change_request.id)

    snapshots = db.scalars(
        select(RepositorySnapshot).where(RepositorySnapshot.id.in_(snapshot_ids))
    ).all()
    used_llm = client is not None and llm_attempted and not llm_stats.unavailable_error
    run.llm_model = client.model if used_llm else None
    run.prompt_version = PROMPT_VERSION if used_llm else None
    run.total_tokens = llm_stats.input_tokens + llm_stats.output_tokens
    run.summary = {
        "requirements": dict(Counter(c.status for c in changes)),
        "findings_by_impact": dict(impacts),
        "findings_by_link": dict(links),
        "abstentions": sum(abstention_reasons.values()),
        "abstention_reasons": dict(abstention_reasons),
        "superseded": superseded,
        "test_suggestions": test_count,
        "candidates_capped": capped,
        "evidence_rejected": rejected_evidence,
        "unresolved_calls": sum(s.unresolved_call_count for s in snapshots),
        "parse_errors": sum(s.parse_error_count for s in snapshots),
        "search": {**search_summary, "inferred_kept": inferred_count},
        "llm": _llm_summary(llm_requested, client, llm_stats, llm_attempted),
        "warnings": warnings,
    }
    run.progress = {"step": "DONE"}
    run.status = RunStatus.COMPLETED
    run.finished_at = utcnow()
    run.duration_ms = int((time.perf_counter() - started) * 1000)

    has_open = db.scalar(
        select(
            exists().where(
                Finding.change_request_id == change_request.id,
                Finding.status == FindingStatus.PROPOSED,
            )
        )
    )
    change_request.status = (
        ChangeRequestStatus.IN_REVIEW if has_open else ChangeRequestStatus.REVIEW_COMPLETED
    )
    record_event(
        db,
        "ANALYSIS_COMPLETED",
        project_id=change_request.project_id,
        actor_id=run.created_by,
        entity_type="analysis_run",
        entity_id=run.id,
        duration_ms=run.duration_ms,
    )


def _mark_failed(db: Session, run: AnalysisRun, reason: str) -> None:
    run.status = RunStatus.FAILED
    run.failure_reason = reason
    run.finished_at = utcnow()
    change_request = db.get(ChangeRequest, run.change_request_id)
    # Gap 3: return to where the request was. A failed re-analysis must not throw a request
    # that already has reviewed findings back to DRAFT.
    change_request.status = run.cr_status_before
    record_event(
        db,
        "ANALYSIS_FAILED",
        project_id=change_request.project_id,
        actor_id=run.created_by,
        entity_type="analysis_run",
        entity_id=run.id,
        reason=reason,
    )


def run_analysis(run_id: int) -> None:
    """Entry point of the background task. It opens its own session, because the session
    of the HTTP request that started it is closed by the time this runs."""
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        run.status = RunStatus.RUNNING
        db.commit()
        try:
            _execute(db, run)
            db.commit()
        except Exception:
            logger.exception("Analysis run %s failed", run_id)
            db.rollback()
            _mark_failed(db, db.get(AnalysisRun, run_id), "Erreur interne pendant l'analyse.")
            db.commit()


def fail_interrupted_runs() -> None:
    """At startup, any run still marked active was killed by the restart (section 1, decision 3)."""
    with SessionLocal() as db:
        interrupted = db.scalars(
            select(AnalysisRun).where(AnalysisRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
        ).all()
        for run in interrupted:
            _mark_failed(db, run, "Interrompue : le serveur a redémarré.")
        db.commit()
