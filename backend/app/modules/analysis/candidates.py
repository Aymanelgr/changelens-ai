"""Candidate generation (pipeline step 6): rules first, then what search adds.

Link type and support are decided here from where the evidence comes from, never by a
model (AI-003, AI-004).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.enums import (
    EvidenceRole,
    EvidenceSource,
    ImpactType,
    LinkType,
    LlmVerdict,
    Support,
    SymbolKind,
    TargetType,
)
from app.modules.code_analysis.analyzer import split_source_lines
from app.modules.code_analysis.models import CallEdge, CodeSymbol
from app.modules.indexing.search import SearchHit
from app.modules.ingestion.models import Segment
from app.modules.requirements.diffing import RequirementChange
from app.modules.requirements.facts import find_value_mentions, has_verifiable_fact
from app.modules.requirements.models import RequirementVersion

_FACTS_WITH_OLD_VALUE = {
    "THRESHOLD_CHANGED",
    "OPERATOR_CHANGED",
    "UNIT_CHANGED",
    "QUANTITY_REMOVED",
}
_FACTS_MAKING_VALUE_OUTDATED = {"THRESHOLD_CHANGED", "QUANTITY_REMOVED", "UNIT_CHANGED"}

# "amount <= 10000" skips the check, which is the same rule as "amount > 10000 triggers it".
# The logical complement of the required operator is therefore consistent, not a contradiction.
_CONSISTENT_OPERATORS = {
    ">": {">", "<="},
    ">=": {">=", "<"},
    "<": {"<", ">="},
    "<=": {"<=", ">"},
    "==": {"==", "!="},
}


@dataclass
class EvidenceData:
    source_type: EvidenceSource
    quote: str
    # The text the quote must appear in. Checked before anything is saved (NFR-004).
    source_text: str
    role: EvidenceRole = EvidenceRole.SUPPORTS
    segment_id: int | None = None
    symbol_id: int | None = None
    requirement_version_id: int | None = None
    document_name: str | None = None
    section_path: str | None = None
    page: int | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    # First line of source_text in its file, to place a quote found later by the LLM.
    source_first_line: int | None = None
    from_llm: bool = False

    @property
    def is_verified(self) -> bool:
        return bool(self.quote) and self.quote in self.source_text


@dataclass
class Candidate:
    req_key: str
    target_type: TargetType
    target_key: str
    target_detail: dict[str, Any]
    impact_type: ImpactType
    link_type: LinkType
    support: Support
    explanation: str
    evidence: list[EvidenceData] = field(default_factory=list)
    from_search: bool = False
    # Filled in by the LLM step. Never decides the link type or the support by itself.
    llm_analyzed: bool = False
    llm_model: str | None = None
    llm_verdict: LlmVerdict | None = None
    llm_explanation: str | None = None
    llm_impact_type: ImpactType | None = None
    llm_uncertainties: list[str] = field(default_factory=list)


@dataclass
class _TargetHits:
    """Everything found about one target for one requirement, merged into one finding."""

    evidence: list[EvidenceData] = field(default_factory=list)
    values: set[str] = field(default_factory=set)
    has_id: bool = False
    has_value: bool = False
    strong_value: bool = False

    def add(self, evidence: EvidenceData) -> None:
        if all(
            (e.quote, e.start_line, e.segment_id)
            != (evidence.quote, evidence.start_line, evidence.segment_id)
            for e in self.evidence
        ):
            self.evidence.append(evidence)


def _id_pattern(req_key: str) -> re.Pattern[str]:
    """REQ-012 also matches REQ_012, req012 and the middle of test_req_012_threshold."""
    prefix, number = req_key.rsplit("-", 1)
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(prefix)}[-_ ]?{re.escape(number)}(?![0-9A-Za-z])",
        re.IGNORECASE,
    )


def _searched_values(facts: Sequence[dict[str, Any]]) -> list[tuple[Any, str | None]]:
    values = []
    for fact in facts:
        if fact["type"] in _FACTS_WITH_OLD_VALUE:
            values.append((fact["old"], fact.get("old_unit")))
    return values


def _value_impact(facts: Sequence[dict[str, Any]], default: ImpactType) -> ImpactType:
    if any(fact["type"] in _FACTS_MAKING_VALUE_OUTDATED for fact in facts):
        return ImpactType.OUTDATED_VALUE
    return default


def _number_equals(literal: Any, value: Any) -> bool:
    return Decimal(str(literal)) == Decimal(str(value))


def _document_candidates(
    change: RequirementChange, segments: Iterable[tuple[Segment, str]]
) -> list[Candidate]:
    pattern = _id_pattern(change.req_key)
    values = _searched_values(change.facts)
    hits: dict[str, _TargetHits] = {}
    details: dict[str, dict[str, Any]] = {}

    for segment, document_name in segments:
        section = segment.section_path
        # Plain text has no sections, so the document itself is the stable target.
        target = f"doc:{document_name}#{section}" if section else f"doc:{document_name}"
        for raw_line in segment.text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            id_found = bool(pattern.search(line))
            mentions = [
                (value, mention)
                for value, unit in values
                for mention in find_value_mentions(line, value, unit)
            ]
            if not id_found and not mentions:
                continue
            bucket = hits.setdefault(target, _TargetHits())
            details[target] = {"document": document_name, "section": section}
            bucket.has_id = bucket.has_id or id_found
            for value, mention in mentions:
                bucket.has_value = True
                bucket.values.add(str(value))
                bucket.strong_value = bucket.strong_value or mention.unit is not None
            bucket.add(
                EvidenceData(
                    source_type=EvidenceSource.SEGMENT,
                    quote=line,
                    source_text=segment.text,
                    segment_id=segment.id,
                    document_name=document_name,
                    section_path=section,
                    page=segment.page,
                )
            )

    candidates = []
    for target, bucket in hits.items():
        location = details[target]["section"] or details[target]["document"]
        if bucket.has_value:
            impact = _value_impact(change.facts, ImpactType.DOC_UPDATE)
            explanation = (
                f"« {location} » mentionne encore l'ancienne valeur "
                f"{', '.join(sorted(bucket.values))} de {change.req_key}."
            )
        else:
            impact = ImpactType.DOC_UPDATE
            explanation = f"« {location} » cite explicitement {change.req_key}."
        candidates.append(
            Candidate(
                req_key=change.req_key,
                target_type=TargetType.DOCUMENT_SECTION,
                target_key=target,
                target_detail=details[target],
                impact_type=impact,
                link_type=LinkType.EXPLICIT if bucket.has_id else LinkType.STATIC,
                support=(
                    Support.STRONG if bucket.has_id or bucket.strong_value else Support.MODERATE
                ),
                explanation=explanation,
                evidence=bucket.evidence,
            )
        )
    return candidates


def _symbol_target(symbol: CodeSymbol) -> tuple[str, dict[str, Any]]:
    return (
        f"code:{symbol.file_path}::{symbol.qualified_name}",
        {
            "file": symbol.file_path,
            "symbol": symbol.qualified_name,
            "lines": [symbol.start_line, symbol.end_line],
            "is_test": symbol.is_test,
        },
    )


def _source_line(symbol: CodeSymbol, line: int) -> str:
    lines = split_source_lines(symbol.source_text)
    index = line - symbol.start_line
    return lines[index].strip() if 0 <= index < len(lines) else ""


def _code_evidence(symbol: CodeSymbol, line: int, role=EvidenceRole.SUPPORTS) -> EvidenceData:
    return EvidenceData(
        source_type=EvidenceSource.SYMBOL,
        quote=_source_line(symbol, line),
        source_text=symbol.source_text,
        role=role,
        symbol_id=symbol.id,
        file_path=symbol.file_path,
        start_line=line,
        end_line=line,
        source_first_line=symbol.start_line,
    )


def _definition_line(symbol: CodeSymbol) -> int | None:
    name = symbol.qualified_name.rsplit(".", 1)[-1]
    for offset, line in enumerate(split_source_lines(symbol.source_text)):
        if re.match(rf"\s*(async\s+def|def|class)\s+{re.escape(name)}\b", line):
            return symbol.start_line + offset
    return None


def _collect_symbol_hits(
    symbol: CodeSymbol,
    pattern: re.Pattern[str],
    values: list[tuple[Any, str | None]],
) -> _TargetHits | None:
    bucket = _TargetHits()

    for literal in symbol.number_literals:
        for value, _unit in values:
            if _number_equals(literal["value"], value):
                bucket.has_value = True
                bucket.values.add(str(value))
                # A value used in a comparison is a rule. A bare constant may be anything.
                bucket.strong_value = bucket.strong_value or literal["compare_op"] is not None
                bucket.add(_code_evidence(symbol, literal["line"]))

    for item in symbol.text_items:
        id_found = bool(pattern.search(item["text"]))
        mentions = [
            (value, mention)
            for value, unit in values
            for mention in find_value_mentions(item["text"], value, unit)
        ]
        if id_found:
            bucket.has_id = True
        for value, mention in mentions:
            bucket.has_value = True
            bucket.values.add(str(value))
            bucket.strong_value = bucket.strong_value or mention.unit is not None
        if id_found or mentions:
            bucket.add(_code_evidence(symbol, item["line"]))

    if symbol.kind != SymbolKind.MODULE and pattern.search(symbol.qualified_name):
        definition = _definition_line(symbol)
        if definition:
            bucket.has_id = True
            bucket.add(_code_evidence(symbol, definition))

    return bucket if bucket.evidence else None


def _code_candidate(
    change: RequirementChange, symbol: CodeSymbol, bucket: _TargetHits
) -> Candidate:
    target, detail = _symbol_target(symbol)
    if symbol.is_test:
        impact = ImpactType.TEST_UPDATE
    elif bucket.has_value:
        impact = _value_impact(change.facts, ImpactType.LOGIC_REVIEW)
    else:
        impact = ImpactType.LOGIC_REVIEW

    if bucket.has_value:
        explanation = (
            f"{symbol.qualified_name} contient l'ancienne valeur "
            f"{', '.join(sorted(bucket.values))} de {change.req_key}."
        )
    else:
        explanation = f"{symbol.qualified_name} cite explicitement {change.req_key}."
    return Candidate(
        req_key=change.req_key,
        target_type=TargetType.CODE_SYMBOL,
        target_key=target,
        target_detail=detail,
        impact_type=impact,
        link_type=LinkType.EXPLICIT if bucket.has_id else LinkType.STATIC,
        support=Support.STRONG if bucket.has_id or bucket.strong_value else Support.MODERATE,
        explanation=explanation,
        evidence=bucket.evidence,
    )


def _operator_for(fact: dict[str, Any], value: Any) -> tuple[str | None, str]:
    """The operator the requirement states for this value, and which version states it."""
    if "new" in fact and _number_equals(fact["new"], value):
        return fact.get("operator"), "nouvelle"
    return fact.get("old_operator"), "ancienne"


def _inconsistency_candidates(
    change: RequirementChange,
    symbols: Iterable[CodeSymbol],
    before: RequirementVersion | None,
    after: RequirementVersion | None,
) -> list[Candidate]:
    """Simple case from section 5.8: same constant, different operator (AC-05)."""
    versions = {"ancienne": before, "nouvelle": after}
    candidates = []
    facts = [f for f in change.facts if f["type"] in {"THRESHOLD_CHANGED", "OPERATOR_CHANGED"}]
    for symbol in symbols:
        if symbol.is_test:
            continue
        contradictions: list[tuple[dict[str, Any], str, str]] = []
        for literal in symbol.number_literals:
            code_operator = literal["compare_op"]
            if code_operator is None:
                continue
            for fact in facts:
                if not (
                    _number_equals(literal["value"], fact["old"])
                    or _number_equals(literal["value"], fact["new"])
                ):
                    continue
                required, version = _operator_for(fact, literal["value"])
                if required in _CONSISTENT_OPERATORS and (
                    code_operator not in _CONSISTENT_OPERATORS[required]
                ):
                    contradictions.append((literal, required, version))
        if not contradictions:
            continue

        target, detail = _symbol_target(symbol)
        literal, required, version = contradictions[0]
        evidence = [
            _code_evidence(symbol, c[0]["line"], EvidenceRole.CONTRADICTS) for c in contradictions
        ]
        # Quote the version the explanation talks about: 10000 is compared with the old text,
        # 5000 with the new one. Citing the wrong one would not support the sentence.
        for name in sorted({c[2] for c in contradictions}):
            requirement = versions[name]
            evidence.append(
                EvidenceData(
                    source_type=EvidenceSource.REQUIREMENT,
                    quote=requirement.text.strip(),
                    source_text=requirement.text,
                    requirement_version_id=requirement.id,
                )
            )
        candidates.append(
            Candidate(
                req_key=change.req_key,
                target_type=TargetType.CODE_SYMBOL,
                target_key=target,
                target_detail=detail,
                impact_type=ImpactType.INCONSISTENCY,
                link_type=LinkType.STATIC,
                support=Support.STRONG,
                explanation=(
                    f"Le code compare avec « {literal['compare_op']} » alors que la {version} "
                    f"version de {change.req_key} exprime « {required} ». "
                    "L'outil ne tranche pas : à arbitrer par le relecteur."
                ),
                evidence=evidence,
            )
        )
    return candidates


def _dependency_candidates(
    change: RequirementChange,
    found: dict[int, CodeSymbol],
    symbols_by_id: dict[int, CodeSymbol],
    edges: Iterable[CallEdge],
) -> list[Candidate]:
    """One hop on the call graph. Never STRONG: a neighbour does not contain the rule itself."""
    results: dict[int, Candidate] = {}

    def add(
        symbol: CodeSymbol, impact: ImpactType, caller: CodeSymbol, line: int, why: str
    ) -> None:
        if symbol.id in found:
            return
        evidence = _code_evidence(caller, line)
        target, detail = _symbol_target(symbol)
        if symbol.id in results:
            results[symbol.id].evidence.append(evidence)
            return
        results[symbol.id] = Candidate(
            req_key=change.req_key,
            target_type=TargetType.CODE_SYMBOL,
            target_key=target,
            target_detail=detail,
            impact_type=impact,
            link_type=LinkType.STATIC,
            support=Support.MODERATE,
            explanation=why,
            evidence=[evidence],
        )

    for edge in edges:
        if edge.callee_id is None:
            continue
        caller = symbols_by_id[edge.caller_id]
        callee = symbols_by_id[edge.callee_id]
        # A test exercising production code does not make that code depend on the rule.
        if caller.id in found and not caller.is_test:
            add(
                callee,
                ImpactType.DEPENDENT_COMPONENT,
                caller,
                edge.line,
                f"{callee.qualified_name} est appelée par {caller.qualified_name}, concernée par "
                f"{change.req_key}. Son comportement peut en dépendre ; "
                "elle ne contient pas la règle.",
            )
        if callee.id in found:
            add(
                caller,
                ImpactType.TEST_UPDATE if caller.is_test else ImpactType.DEPENDENT_COMPONENT,
                caller,
                edge.line,
                f"{caller.qualified_name} appelle {callee.qualified_name}, concernée par "
                f"{change.req_key}. À vérifier avec la nouvelle règle.",
            )
    return list(results.values())


def _priority(candidate: Candidate) -> tuple:
    return (
        candidate.impact_type != ImpactType.INCONSISTENCY,
        candidate.link_type == LinkType.INFERRED,
        candidate.link_type != LinkType.EXPLICIT,
        candidate.support != Support.STRONG,
        candidate.target_key,
        candidate.impact_type,
    )


def cap_candidates(candidates: list[Candidate], maximum: int) -> tuple[list[Candidate], int]:
    """Returns (kept, number dropped). The cap is reported, never silent."""
    ordered = sorted(candidates, key=_priority)
    return ordered[:maximum], max(0, len(ordered) - maximum)


def deterministic_candidates(
    change: RequirementChange,
    segments: Sequence[tuple[Segment, str]],
    symbols: Sequence[CodeSymbol],
    edges: Sequence[CallEdge],
    before: RequirementVersion | None,
    after: RequirementVersion | None,
) -> list[Candidate]:
    candidates = _document_candidates(change, segments)
    pattern = _id_pattern(change.req_key)
    values = _searched_values(change.facts)
    found: dict[int, CodeSymbol] = {}
    for symbol in symbols:
        bucket = _collect_symbol_hits(symbol, pattern, values)
        if bucket:
            found[symbol.id] = symbol
            candidates.append(_code_candidate(change, symbol, bucket))
    candidates += _inconsistency_candidates(change, symbols, before, after)
    candidates += _dependency_candidates(change, found, {s.id: s for s in symbols}, edges)
    return candidates


# Subjective wording, which no document, code or test can be checked against (section 5.7, rule 4).
_VAGUE_RE = re.compile(
    r"\b(?:agr[ée]able|intuiti(?:f|ve)|convivial|ergonomique|moderne|fluide|meilleure?s?"
    r"|plus\s+(?:simple|clair|claire|rapide|efficace|joli|jolie|beau|belle)|satisfaisante?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W\d_]{4,}|\d+", re.UNICODE)
_STEM_LENGTH = 6


def is_vague_change(change: RequirementChange) -> bool:
    """Vague wording and nothing measurable: the change itself cannot be traced to anything."""
    if has_verifiable_fact(change.facts):
        return False
    return bool(_VAGUE_RE.search(change.new_text or ""))


def _stems(text: str) -> set[str]:
    return {word.lower()[:_STEM_LENGTH] for word in _WORD_RE.findall(text)}


def _best_line(text: str, query_stems: set[str]) -> tuple[int, str] | None:
    """The line sharing the most words with the requirement, first one on a tie. This is what a
    reviewer reads first, so it must be chosen the same way on every run."""
    best: tuple[int, str] | None = None
    best_score = -1
    for index, raw in enumerate(split_source_lines(text)):
        line = raw.strip()
        if not line:
            continue
        score = len(_stems(line) & query_stems)
        if score > best_score:
            best, best_score = (index, line), score
    return best


def _states_new_value(text: str, facts: Sequence[dict[str, Any]]) -> bool:
    """A passage already stating the new value is up to date: nothing to review there."""
    return any(
        fact["type"] == "THRESHOLD_CHANGED" and find_value_mentions(text, fact["new"], fact["unit"])
        for fact in facts
    )


def _search_evidence(hit: SearchHit, query_stems: set[str]) -> EvidenceData | None:
    if hit.kind == "segment":
        segment: Segment = hit.row
        found = _best_line(segment.text, query_stems)
        if found is None:
            return None
        return EvidenceData(
            source_type=EvidenceSource.SEGMENT,
            quote=found[1],
            source_text=segment.text,
            segment_id=segment.id,
            document_name=hit.document_name,
            section_path=segment.section_path,
            page=segment.page,
        )
    symbol: CodeSymbol = hit.row
    found = _best_line(symbol.source_text, query_stems)
    return _code_evidence(symbol, symbol.start_line + found[0]) if found else None


def search_candidates(
    change: RequirementChange,
    hits: Sequence[SearchHit],
    taken: set[str],
    min_cosine: float,
    strong_cosine: float,
) -> list[Candidate]:
    """Passages search found and rules did not. They are INFERRED: to be confirmed, and never
    STRONG, because resembling a requirement proves nothing (section 5.5)."""
    query = " ".join(filter(None, [change.title, change.old_text, change.new_text]))
    query_stems = _stems(query)
    candidates = []
    for hit in hits:
        if hit.target_key in taken:
            continue
        text = hit.row.text if hit.kind == "segment" else hit.row.source_text
        if text.lstrip().startswith("#") and "\n" not in text.strip():
            continue  # a heading alone: nothing there to review
        # Without vectors, a keyword result needs several shared words to count as relevant.
        relevant = (
            hit.cosine >= min_cosine
            if hit.cosine is not None
            else len(_stems(text) & query_stems) >= 3
        )
        if not relevant or _states_new_value(text, change.facts):
            continue
        evidence = _search_evidence(hit, query_stems)
        if evidence is None:
            continue

        if hit.kind == "segment":
            target_type = TargetType.DOCUMENT_SECTION
            detail = {"document": hit.document_name, "section": hit.row.section_path}
            if hit.row.page is not None:
                detail["page"] = hit.row.page
            impact = ImpactType.DOC_UPDATE
        else:
            target_type = TargetType.CODE_SYMBOL
            _, detail = _symbol_target(hit.row)
            impact = ImpactType.TEST_UPDATE if hit.row.is_test else ImpactType.LOGIC_REVIEW

        in_both = hit.keyword_rank is not None and hit.vector_rank is not None
        support = (
            Support.MODERATE
            if in_both and hit.cosine is not None and hit.cosine >= strong_cosine
            else Support.WEAK
        )
        candidates.append(
            Candidate(
                req_key=change.req_key,
                target_type=target_type,
                target_key=hit.target_key,
                target_detail=detail,
                impact_type=impact,
                link_type=LinkType.INFERRED,
                support=support,
                explanation=(
                    f"Proposé par la recherche : ce passage semble traiter du même sujet que "
                    f"{change.req_key}. Aucune valeur ni identifiant ne le prouve : à confirmer."
                ),
                evidence=[evidence],
                from_search=True,
            )
        )
    return candidates


def abstention_candidate(change: RequirementChange, requirement: RequirementVersion) -> Candidate:
    """Rules 1 and 4 of section 5.7: say so instead of guessing (AC-06)."""
    return Candidate(
        req_key=change.req_key,
        target_type=TargetType.REQUIREMENT,
        target_key=f"requirement:{change.req_key}",
        target_detail={"requirement": change.req_key},
        impact_type=ImpactType.NONE,
        link_type=LinkType.INSUFFICIENT_EVIDENCE,
        support=Support.WEAK,
        explanation="Impossible d'établir un lien fiable ; précisez le critère attendu.",
        evidence=[
            EvidenceData(
                source_type=EvidenceSource.REQUIREMENT,
                quote=requirement.text.strip(),
                source_text=requirement.text,
                requirement_version_id=requirement.id,
            )
        ],
    )
