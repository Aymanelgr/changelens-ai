"""Connects the candidates to the LLM (pipeline steps 7 and 8).

The model reads and explains. What it may change is limited on purpose:
- a search candidate it does not support is dropped, one it supports reaches MODERATE at most;
- a rule-based candidate always stays on screen, whatever the model says (section 3, step 8).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.enums import EvidenceRole, ImpactType, LlmVerdict, Support
from app.llm.analysis import Excerpt, LlmFinding, analyze_batch
from app.llm.client import LLMClient, LlmUnavailable
from app.modules.analysis.candidates import Candidate, EvidenceData
from app.modules.code_analysis.analyzer import split_source_lines
from app.modules.requirements.diffing import RequirementChange


@dataclass
class LlmStats:
    analyzed: int = 0
    discarded: int = 0
    validation_failures: int = 0
    retries: int = 0
    rejected: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unavailable_error: str | None = None
    errors: list[str] = field(default_factory=list)


def _window(text: str, quote: str, limit: int) -> str:
    """The excerpt keeps the evidence in view and stays short, to control the model's time."""
    if len(text) <= limit:
        return text
    position = max(text.find(quote), 0)
    start = max(0, position - limit // 3)
    return text[start : start + limit]


def build_excerpt(candidate: Candidate, evidence_id: str, limit: int) -> Excerpt:
    primary = candidate.evidence[0]
    if primary.file_path:
        detail = candidate.target_detail
        kind = "test" if detail.get("is_test") else "code"
        header = (
            f"{kind}: {detail.get('file')} | {detail.get('symbol')} "
            f"| lignes {detail.get('lines', ['?', '?'])[0]}-{detail.get('lines', ['?', '?'])[1]}"
        )
        # The evidence for a caller or callee is the other function: the model must know that.
        if candidate.impact_type == ImpactType.DEPENDENT_COMPONENT:
            header += f" | extrait de la fonction liée, dans {primary.file_path}"
    elif primary.document_name:
        header = (
            f"doc: {primary.document_name} | section: {primary.section_path or '-'} "
            f"| page: {primary.page or '-'}"
        )
    else:
        header = f"exigence: {candidate.req_key}"
    return Excerpt(evidence_id, header, _window(primary.source_text, primary.quote, limit))


def _llm_evidence(primary: EvidenceData, quote: str, role: str) -> EvidenceData:
    line = None
    if primary.source_first_line is not None:
        for offset, source_line in enumerate(split_source_lines(primary.source_text)):
            if quote in source_line:
                line = primary.source_first_line + offset
                break
    return EvidenceData(
        source_type=primary.source_type,
        quote=quote,
        source_text=primary.source_text,
        role=EvidenceRole(role),
        segment_id=primary.segment_id,
        symbol_id=primary.symbol_id,
        requirement_version_id=primary.requirement_version_id,
        document_name=primary.document_name,
        section_path=primary.section_path,
        page=primary.page,
        file_path=primary.file_path,
        start_line=line,
        end_line=line,
        source_first_line=primary.source_first_line,
        from_llm=True,
    )


def _apply_verdict(
    candidate: Candidate, verdict: LlmFinding, by_id: dict[str, Candidate], model: str
) -> None:
    candidate.llm_analyzed = True
    candidate.llm_model = model
    candidate.llm_verdict = LlmVerdict(verdict.verdict)
    candidate.llm_explanation = verdict.explanation.strip()
    candidate.llm_impact_type = ImpactType(verdict.impact_type)
    candidate.llm_uncertainties = [item.strip() for item in verdict.uncertainties if item.strip()]
    known = {(e.quote, e.source_text) for e in candidate.evidence}
    for item in verdict.evidence:
        primary = by_id[item.evidence_id].evidence[0]
        quote = item.quote.strip()
        if (quote, primary.source_text) not in known:
            known.add((quote, primary.source_text))
            candidate.evidence.append(_llm_evidence(primary, quote, item.role))


def apply_llm(
    client: LLMClient,
    change: RequirementChange,
    candidates: list[Candidate],
    stats: LlmStats,
    on_batch: Callable[[], None] | None = None,
) -> list[Candidate]:
    """Returns the candidates that survive. Raises LlmUnavailable if the model cannot be reached:
    candidates are then returned to the caller untouched, as degraded mode requires."""
    settings = get_settings()
    size = max(1, settings.llm_batch_size)
    numbered = {f"E{i}": candidate for i, candidate in enumerate(candidates, start=1)}
    ids = list(numbered)
    verdicts: dict[str, LlmFinding] = {}
    for start in range(0, len(ids), size):
        batch_ids = ids[start : start + size]
        excerpts = [
            build_excerpt(numbered[eid], eid, settings.llm_excerpt_chars) for eid in batch_ids
        ]
        result = analyze_batch(
            client, change.req_key, change.old_text, change.new_text, change.facts, excerpts
        )
        stats.input_tokens += result.input_tokens
        stats.output_tokens += result.output_tokens
        stats.retries += result.attempts - 1
        stats.rejected += result.rejected_quotes
        if result.failed:
            stats.validation_failures += 1
        stats.errors += result.errors[:3]
        verdicts.update(result.verdicts)
        if on_batch:
            on_batch()

    kept = []
    for evidence_id, candidate in numbered.items():
        verdict = verdicts.get(evidence_id)
        if verdict is not None:
            _apply_verdict(candidate, verdict, numbered, client.model)
            stats.analyzed += 1
        if not candidate.from_search:
            kept.append(candidate)  # a rule found it: it stays, with or without the model
        elif verdict is not None and verdict.verdict == "LIKELY_IMPACTED":
            # Capped at MODERATE: semantic proximity plus a model's opinion is still not proof.
            candidate.support = Support.MODERATE
            kept.append(candidate)
        else:
            stats.discarded += 1
    return kept


__all__ = ["LlmStats", "LlmUnavailable", "apply_llm", "build_excerpt"]
