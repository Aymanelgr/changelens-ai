"""Builds the impact report (FR-015). One data structure, two renderings: they cannot disagree.

Honesty rules enforced here: every finding carries its citations, a suggested test is never
shown as executed, an incomplete review says so, and the known limits are always printed.
"""

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.enums import FindingStatus, LinkType, RunInputType
from app.modules.analysis.models import (
    AnalysisRun,
    Evidence,
    Finding,
    FindingOccurrence,
    RunInput,
    TestSuggestion,
)
from app.modules.auth.models import User
from app.modules.code_analysis.models import CallEdge, RepositorySnapshot
from app.modules.ingestion.models import Document, DocumentVersion
from app.modules.projects.models import Project
from app.modules.requirements.diffing import RequirementChange, diff_requirements
from app.modules.requirements.models import ChangeRequest, RequirementSet, RequirementVersion
from app.modules.review.models import ReviewDecision

_STATUS_TITLES = {
    FindingStatus.PROPOSED: "En attente de décision",
    FindingStatus.ACCEPTED: "Acceptés",
    FindingStatus.CORRECTED: "Corrigés",
    FindingStatus.REJECTED: "Rejetés",
    FindingStatus.SUPERSEDED: "Remplacés par une analyse plus récente",
}
_IMPACT_LABELS = {
    "OUTDATED_VALUE": "Valeur obsolète",
    "LOGIC_REVIEW": "Logique à revoir",
    "TEST_UPDATE": "Test à adapter",
    "DOC_UPDATE": "Documentation à revoir",
    "DEPENDENT_COMPONENT": "Composant dépendant",
    "INCONSISTENCY": "Incohérence",
    "NONE": "Aucun impact établi",
}
_VERDICT_LABELS = {
    "LIKELY_IMPACTED": "probablement impacté",
    "NOT_IMPACTED": "non concerné",
    "INSUFFICIENT_EVIDENCE": "preuves insuffisantes",
}
_DECISION_LABELS = {"ACCEPT": "acceptation", "REJECT": "rejet", "CORRECT": "correction"}
KNOWN_LIMITS = [
    "L'analyse statique est une approximation : appels dynamiques (getattr, eval), injection de "
    "dépendances et méthodes d'objets de type inconnu ne sont pas suivis.",
    "Une valeur lue au lancement dans un fichier de configuration ou en base est invisible.",
    "Une constante utilisée indirectement n'est reliée à la fonction qui l'utilise que si la "
    "recherche ou le LLM la retrouve.",
    "Un finding signifie « probablement à examiner » : il ne prouve jamais qu'une exigence est "
    "correctement implémentée.",
    "Les tests suggérés n'ont été ni écrits ni exécutés par l'outil.",
]


@dataclass
class ReportEvidence:
    role: str
    location: str
    quote: str
    from_llm: bool


@dataclass
class ReportDecision:
    reviewer: str
    decision: str
    reason: str | None
    at: datetime


@dataclass
class ReportFinding:
    req_key: str
    status: FindingStatus
    target: str
    impact: str
    link_type: str
    support: str
    explanation: str
    llm_explanation: str | None
    llm_verdict: str | None
    llm_model: str | None
    uncertainties: list[str]
    corrected_target: str | None
    corrected_explanation: str | None
    stale: bool
    evidence: list[ReportEvidence]
    decisions: list[ReportDecision] = field(default_factory=list)


@dataclass
class ReportData:
    project: str
    title: str
    run: AnalysisRun
    author: str
    changes: list[RequirementChange]
    documents: list[str]
    snapshots: list[str]
    requirement_sets: list[str]
    findings: list[ReportFinding]
    tests: list[TestSuggestion]
    unresolved: list[str]

    @property
    def open_count(self) -> int:
        return sum(1 for finding in self.findings if finding.status == FindingStatus.PROPOSED)

    @property
    def abstentions(self) -> list[ReportFinding]:
        return [f for f in self.findings if f.link_type == LinkType.INSUFFICIENT_EVIDENCE]

    @property
    def impacts(self) -> list[ReportFinding]:
        return [f for f in self.findings if f.link_type != LinkType.INSUFFICIENT_EVIDENCE]


def _target_label(finding: Finding) -> str:
    detail = finding.target_detail
    if detail.get("file"):
        first, last = detail["lines"]
        return f"{detail['file']} · {detail['symbol']} (lignes {first}-{last})"
    if detail.get("document"):
        section = f" · {detail['section']}" if detail.get("section") else ""
        return f"{detail['document']}{section}"
    return detail.get("requirement", finding.target_key)


def _evidence_location(evidence: Evidence) -> str:
    if evidence.file_path:
        return f"{evidence.file_path}, ligne {evidence.start_line}"
    if evidence.document_name:
        section = f", section {evidence.section_path}" if evidence.section_path else ""
        page = f", page {evidence.page}" if evidence.page else ""
        return f"{evidence.document_name}{section}{page}"
    return "texte de l'exigence"


def collect(db: Session, run: AnalysisRun) -> ReportData:
    change_request = db.get(ChangeRequest, run.change_request_id)
    project = db.get(Project, change_request.project_id)
    author = db.get(User, run.created_by)

    def requirements(set_id: int) -> list[RequirementVersion]:
        return list(
            db.scalars(
                select(RequirementVersion).where(RequirementVersion.requirement_set_id == set_id)
            )
        )

    changes = [
        change
        for change in diff_requirements(
            requirements(change_request.set_before_id), requirements(change_request.set_after_id)
        )
        if change.status != "UNCHANGED"
    ]

    inputs = db.scalars(select(RunInput).where(RunInput.run_id == run.id)).all()
    ids = {kind: [i.version_id for i in inputs if i.input_type == kind] for kind in RunInputType}
    documents = [
        f"{name} v{version.number} ({version.sha256[:12]})"
        for version, name in db.execute(
            select(DocumentVersion, Document.name)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(ids[RunInputType.DOCUMENT_VERSION]))
            .order_by(Document.name)
        )
    ]
    snapshots = [
        f"{snapshot.archive_name} ({snapshot.sha256[:12]})"
        for snapshot in db.scalars(
            select(RepositorySnapshot).where(
                RepositorySnapshot.id.in_(ids[RunInputType.REPOSITORY_SNAPSHOT])
            )
        )
    ]
    set_ids = [
        *ids[RunInputType.REQUIREMENT_SET_BEFORE],
        *ids[RunInputType.REQUIREMENT_SET_AFTER],
    ]
    requirement_sets = [
        f"version {item.number} : {item.source_name} ({item.sha256[:12]})"
        for item in db.scalars(
            select(RequirementSet)
            .where(RequirementSet.id.in_(set_ids))
            .order_by(RequirementSet.number)
        )
    ]

    occurrences = db.scalars(
        select(FindingOccurrence)
        .join(Finding, Finding.id == FindingOccurrence.finding_id)
        .options(joinedload(FindingOccurrence.finding), selectinload(FindingOccurrence.evidence))
        .where(FindingOccurrence.run_id == run.id)
        .order_by(Finding.req_key, Finding.target_key)
    ).all()
    finding_ids = [occurrence.finding_id for occurrence in occurrences]
    decisions: dict[int, list[ReportDecision]] = {}
    for decision, email in db.execute(
        select(ReviewDecision, User.email)
        .join(User, User.id == ReviewDecision.reviewer_id)
        .where(ReviewDecision.finding_id.in_(finding_ids))
        .order_by(ReviewDecision.id)
    ):
        decisions.setdefault(decision.finding_id, []).append(
            ReportDecision(email, decision.decision, decision.reason, decision.created_at)
        )

    findings = [
        ReportFinding(
            req_key=occurrence.finding.req_key,
            status=occurrence.finding.status,
            target=_target_label(occurrence.finding),
            impact=_IMPACT_LABELS[occurrence.finding.impact_type],
            link_type=occurrence.link_type,
            support=occurrence.support,
            explanation=occurrence.explanation,
            llm_explanation=occurrence.llm_explanation,
            llm_verdict=occurrence.llm_verdict,
            llm_model=occurrence.llm_model,
            uncertainties=occurrence.llm_uncertainties,
            corrected_target=occurrence.finding.corrected_target,
            corrected_explanation=occurrence.finding.corrected_explanation,
            stale=occurrence.finding.stale,
            evidence=[
                ReportEvidence(e.role, _evidence_location(e), e.quote, e.from_llm)
                for e in occurrence.evidence
            ],
            decisions=decisions.get(occurrence.finding_id, []),
        )
        for occurrence in occurrences
    ]
    unresolved = sorted(
        {
            f"{edge.callee_name} (ligne {edge.line})"
            for edge in db.scalars(
                select(CallEdge).where(
                    CallEdge.snapshot_id.in_(ids[RunInputType.REPOSITORY_SNAPSHOT]),
                    CallEdge.resolved.is_(False),
                    CallEdge.external.is_(False),
                )
            )
        }
    )
    return ReportData(
        project=project.name,
        title=change_request.title,
        run=run,
        author=author.email,
        changes=changes,
        documents=documents,
        snapshots=snapshots,
        requirement_sets=requirement_sets,
        findings=findings,
        tests=list(
            db.scalars(
                select(TestSuggestion)
                .where(TestSuggestion.run_id == run.id)
                .order_by(TestSuggestion.id)
            )
        ),
        unresolved=unresolved,
    )


def _fact_text(fact: dict[str, Any]) -> str:
    kind = fact["type"]
    if kind == "THRESHOLD_CHANGED":
        changed = (
            f"opérateur « {fact['old_operator']} » devenu « {fact['operator']} »"
            if fact.get("operator_changed")
            else f"opérateur « {fact.get('operator')} » inchangé"
        )
        return f"Seuil {fact['old']} → {fact['new']} {fact.get('unit') or ''}, {changed}"
    if kind == "OPERATOR_CHANGED":
        return f"Opérateur « {fact['old_operator']} » → « {fact['operator']} » sur {fact['new']}"
    if kind == "UNIT_CHANGED":
        return f"Unité modifiée sur {fact['new']} : {fact.get('old_unit')} → {fact.get('unit')}"
    if kind == "QUANTITY_ADDED":
        return f"Valeur ajoutée : {fact['new']} {fact.get('unit') or ''}"
    if kind == "QUANTITY_REMOVED":
        return f"Valeur retirée : {fact['old']} {fact.get('old_unit') or ''}"
    if kind == "MODALITY_CHANGED":
        return f"Modalité : {fact['old'] or 'aucune'} → {fact['new'] or 'aucune'}"
    return "Texte modifié, aucun fait vérifiable"


def _llm_line(data: ReportData) -> str:
    summary = data.run.summary.get("llm", {})
    if data.run.llm_model:
        where = "local" if summary.get("where") == "local" else "API distante"
        return (
            f"{data.run.llm_model} ({where}), prompt {data.run.prompt_version}, "
            f"température {data.run.params.get('llm_temperature')}"
        )
    return f"aucun : {summary.get('label', 'non analysé par le LLM')}"


def render_markdown(data: ReportData) -> str:
    run = data.run
    lines = [
        f"# Rapport d'impact : {data.title}",
        "",
        f"- **Projet :** {data.project}",
        f"- **Analyse :** n° {run.id}, lancée par {data.author}, "
        f"le {run.created_at:%d/%m/%Y à %H:%M} UTC, durée {run.duration_ms} ms",
        f"- **Modèle LLM :** {_llm_line(data)}",
        f"- **Modèle d'embeddings :** {run.embedding_model or 'aucun (mots-clés uniquement)'}",
    ]
    if data.open_count:
        lines += ["", f"> **Revue incomplète :** {data.open_count} finding(s) encore à décider."]

    lines += ["", "## Changement et faits détectés", ""]
    for change in data.changes:
        lines.append(f"### {change.req_key} ({change.status}) : {change.title}")
        if change.old_text:
            lines.append(f"- Avant : {change.old_text}")
        if change.new_text:
            lines.append(f"- Après : {change.new_text}")
        lines += [f"- Fait : {_fact_text(fact)}" for fact in change.facts]
        lines.append("")

    lines += ["## Versions analysées", ""]
    lines += [f"- Exigences, {item}" for item in data.requirement_sets]
    lines += [f"- Document : {item}" for item in data.documents]
    lines += [f"- Code : {item}" for item in data.snapshots]

    lines += ["", "## Findings par statut", ""]
    for status, title in _STATUS_TITLES.items():
        selected = [f for f in data.impacts if f.status == status]
        if not selected:
            continue
        lines += [f"### {title} ({len(selected)})", ""]
        for finding in selected:
            lines += _finding_markdown(finding)

    lines += ["## Abstentions", ""]
    if data.abstentions:
        for finding in data.abstentions:
            lines.append(f"- **{finding.req_key}** : {finding.explanation}")
            lines += [f"  - Citation : « {e.quote} »" for e in finding.evidence]
    else:
        lines.append("Aucune.")

    lines += [
        "",
        "## Tests suggérés",
        "",
        "Statut SUGGESTED : aucun de ces tests n'a été exécuté.",
        "",
    ]
    if data.tests:
        lines += ["| Exigence | Entrée | Résultat attendu | Statut |", "|---|---|---|---|"]
        lines += [
            f"| {t.req_key} | {t.input_value} | {t.expected} | {t.status} |" for t in data.tests
        ]
    else:
        lines.append("Aucun.")

    lines += ["", "## Appels non résolus par l'analyse statique", ""]
    if data.unresolved:
        lines.append(f"{len(data.unresolved)} appel(s) : du code dépendant peut manquer.")
        lines += [f"- `{item}`" for item in data.unresolved]
    else:
        lines.append("Aucun.")

    lines += ["", "## Limites connues", ""]
    lines += [f"- {limit}" for limit in KNOWN_LIMITS]
    return "\n".join(lines) + "\n"


def _finding_markdown(finding: ReportFinding) -> list[str]:
    lines = [
        f"#### {finding.req_key} · {finding.target}",
        f"- {finding.impact} · lien {finding.link_type} · support {finding.support}"
        + (" · **à réanalyser**" if finding.stale else ""),
        f"- Règle : {finding.explanation}",
    ]
    if finding.llm_explanation:
        verdict = _VERDICT_LABELS.get(finding.llm_verdict, finding.llm_verdict)
        lines.append(f"- LLM ({finding.llm_model}, {verdict}) : {finding.llm_explanation}")
        lines += [f"  - Incertain : {item}" for item in finding.uncertainties]
    else:
        lines.append("- Non analysé par le LLM.")
    if finding.corrected_target:
        lines.append(f"- Cible corrigée : {finding.corrected_target}")
    if finding.corrected_explanation:
        lines.append(f"- Explication corrigée : {finding.corrected_explanation}")
    for evidence in finding.evidence:
        contradiction = " (contredit)" if evidence.role == "CONTRADICTS" else ""
        origin = " [citée par le LLM]" if evidence.from_llm else ""
        lines += [
            f"- Preuve{contradiction}{origin} : {evidence.location}",
            "",
            "  ```text",
            f"  {evidence.quote}",
            "  ```",
            "",
        ]
    for decision in finding.decisions:
        reason = f", motif : {decision.reason}" if decision.reason else ""
        lines.append(
            f"- Décision : {_DECISION_LABELS[decision.decision]} par {decision.reviewer} "
            f"le {decision.at:%d/%m/%Y à %H:%M} UTC{reason}"
        )
    lines.append("")
    return lines


_STYLE = """
:root { color-scheme: light; }
body { font: 15px/1.55 Georgia, 'Times New Roman', serif; color: #1c1a17; background: #fff;
  max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; overflow-wrap: anywhere; }
h1 { font-size: 26px; line-height: 1.2; margin: 0 0 12px; }
h2 { font-size: 18px; margin: 32px 0 10px; padding-bottom: 4px; border-bottom: 2px solid #1c1a17; }
h3 { font-size: 15px; margin: 22px 0 8px; }
h4 { font-size: 14px; margin: 0 0 4px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; margin: 0; }
dt { font-weight: 700; } dd { margin: 0; }
.incomplete { border-left: 4px solid #9a5b00; background: #fbf3e2; padding: 8px 12px;
  margin: 16px 0; }
.finding { border-top: 1px solid #c9c3b8; padding: 12px 0; break-inside: avoid; }
.meta { font: 12px/1.5 Consolas, 'Courier New', monospace; color: #5c564c; }
pre { font: 13px/1.5 Consolas, 'Courier New', monospace; font-variant-ligatures: none;
  white-space: pre-wrap; background: #f4f1ea; padding: 6px 10px; margin: 4px 0 8px; }
.where { font-size: 12px; color: #5c564c; margin: 8px 0 0; }
.contradicts pre { background: #f9e7e3; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #c9c3b8;
  vertical-align: top; }
@media print { body { padding: 0; } }
"""


def _e(value: Any) -> str:
    """Everything here comes from imported files. Escaped without exception."""
    return html.escape(str(value), quote=True)


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>"


def _finding_html(finding: ReportFinding) -> str:
    parts = [
        '<article class="finding">',
        f"<h4>{_e(finding.req_key)} · {_e(finding.target)}</h4>",
        f'<p class="meta">{_e(finding.impact)} · lien {_e(finding.link_type)} '
        f"· support {_e(finding.support)}{' · à réanalyser' if finding.stale else ''}</p>",
        f"<p>{_e(finding.explanation)}</p>",
    ]
    if finding.llm_explanation:
        verdict = _VERDICT_LABELS.get(finding.llm_verdict, finding.llm_verdict)
        parts.append(
            f"<p><strong>LLM ({_e(finding.llm_model)}, {_e(verdict)}) :</strong> "
            f"{_e(finding.llm_explanation)}</p>"
        )
        if finding.uncertainties:
            parts.append(f"<p>Incertain :</p>{_list(finding.uncertainties)}")
    else:
        parts.append('<p class="meta">Non analysé par le LLM.</p>')
    if finding.corrected_target:
        parts.append(f"<p><strong>Cible corrigée :</strong> {_e(finding.corrected_target)}</p>")
    if finding.corrected_explanation:
        parts.append(
            f"<p><strong>Explication corrigée :</strong> {_e(finding.corrected_explanation)}</p>"
        )
    for evidence in finding.evidence:
        css = ' class="contradicts"' if evidence.role == "CONTRADICTS" else ""
        note = " · contredit" if evidence.role == "CONTRADICTS" else ""
        note += " · citée par le LLM" if evidence.from_llm else ""
        parts.append(
            f'<div{css}><p class="where">{_e(evidence.location)}{note}</p>'
            f"<pre>{_e(evidence.quote)}</pre></div>"
        )
    for decision in finding.decisions:
        reason = f", motif : {_e(decision.reason)}" if decision.reason else ""
        parts.append(
            f"<p><strong>Décision :</strong> {_DECISION_LABELS[decision.decision]} par "
            f"{_e(decision.reviewer)} le {decision.at:%d/%m/%Y à %H:%M} UTC{reason}</p>"
        )
    parts.append("</article>")
    return "".join(parts)


def render_html(data: ReportData) -> str:
    run = data.run
    parts = [
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Rapport d'impact : {_e(data.title)}</title><style>{_STYLE}</style></head><body>",
        f"<h1>Rapport d'impact : {_e(data.title)}</h1><dl>",
        f"<dt>Projet</dt><dd>{_e(data.project)}</dd>",
        f"<dt>Analyse</dt><dd>n° {run.id}, lancée par {_e(data.author)}, le "
        f"{run.created_at:%d/%m/%Y à %H:%M} UTC, durée {_e(run.duration_ms)} ms</dd>",
        f"<dt>Modèle LLM</dt><dd>{_e(_llm_line(data))}</dd>",
        f"<dt>Embeddings</dt><dd>{_e(run.embedding_model or 'aucun (mots-clés uniquement)')}</dd>",
        "</dl>",
    ]
    if data.open_count:
        parts.append(
            f'<p class="incomplete"><strong>Revue incomplète :</strong> {data.open_count} '
            "finding(s) encore à décider.</p>"
        )
    parts.append("<h2>Changement et faits détectés</h2>")
    for change in data.changes:
        parts.append(f"<h3>{_e(change.req_key)} ({_e(change.status)}) : {_e(change.title)}</h3>")
        items = []
        if change.old_text:
            items.append(f"Avant : {change.old_text}")
        if change.new_text:
            items.append(f"Après : {change.new_text}")
        items += [f"Fait : {_fact_text(fact)}" for fact in change.facts]
        parts.append(_list(items))

    parts.append("<h2>Versions analysées</h2>")
    parts.append(
        _list(
            [
                *[f"Exigences, {item}" for item in data.requirement_sets],
                *[f"Document : {item}" for item in data.documents],
                *[f"Code : {item}" for item in data.snapshots],
            ]
        )
    )
    parts.append("<h2>Findings par statut</h2>")
    for status, title in _STATUS_TITLES.items():
        selected = [f for f in data.impacts if f.status == status]
        if selected:
            parts.append(f"<h3>{_e(title)} ({len(selected)})</h3>")
            parts += [_finding_html(finding) for finding in selected]

    parts.append("<h2>Abstentions</h2>")
    if data.abstentions:
        for finding in data.abstentions:
            quotes = "".join(f"<pre>{_e(e.quote)}</pre>" for e in finding.evidence)
            parts.append(
                f"<p><strong>{_e(finding.req_key)} :</strong> {_e(finding.explanation)}</p>{quotes}"
            )
    else:
        parts.append("<p>Aucune.</p>")

    parts.append(
        "<h2>Tests suggérés</h2><p>Statut SUGGESTED : aucun de ces tests n'a été exécuté.</p>"
    )
    if data.tests:
        rows = "".join(
            f"<tr><td>{_e(t.req_key)}</td><td>{_e(t.input_value)}</td>"
            f"<td>{_e(t.expected)}</td><td>{_e(t.status)}</td></tr>"
            for t in data.tests
        )
        parts.append(
            "<table><thead><tr><th>Exigence</th><th>Entrée</th><th>Résultat attendu</th>"
            f"<th>Statut</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    else:
        parts.append("<p>Aucun.</p>")

    parts.append("<h2>Appels non résolus par l'analyse statique</h2>")
    if data.unresolved:
        parts.append(f"<p>{len(data.unresolved)} appel(s) : du code dépendant peut manquer.</p>")
        parts.append(_list(data.unresolved))
    else:
        parts.append("<p>Aucun.</p>")
    parts.append("<h2>Limites connues</h2>")
    parts.append(_list(KNOWN_LIMITS))
    parts.append("</body></html>")
    return "".join(parts)
