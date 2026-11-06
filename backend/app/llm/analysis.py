"""Evidence-anchored analysis (pipeline steps 7 and 8).

The model only reads numbered excerpts and answers about them. Its output is then checked: valid
JSON, known identifiers, and every quote present word for word in the excerpt it cites. One retry
with the errors spelled out, then whatever still fails is rejected and counted (AI-002).
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.llm.client import LLMClient

_PROMPT_NAME = "impact_analysis_v1"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / f"{_PROMPT_NAME}.md").read_text(
    encoding="utf-8"
)
# AI-008: the recorded version includes a hash, so editing the prompt without renaming it still
# shows up. Two runs with the same version used the same words.
PROMPT_VERSION = f"{_PROMPT_NAME}+{hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:8]}"

_EXPLANATION_MAX = 400


class LlmEvidence(BaseModel):
    evidence_id: str
    quote: str
    role: Literal["SUPPORTS", "CONTRADICTS"]


class LlmFinding(BaseModel):
    candidate_id: str
    verdict: Literal["LIKELY_IMPACTED", "NOT_IMPACTED", "INSUFFICIENT_EVIDENCE"]
    impact_type: Literal[
        "OUTDATED_VALUE",
        "LOGIC_REVIEW",
        "TEST_UPDATE",
        "DOC_UPDATE",
        "DEPENDENT_COMPONENT",
        "INCONSISTENCY",
        "NONE",
    ]
    explanation: str = Field(max_length=_EXPLANATION_MAX)
    evidence: list[LlmEvidence]
    uncertainties: list[str]


class LlmOutput(BaseModel):
    findings: list[LlmFinding]


def output_schema() -> dict[str, Any]:
    return LlmOutput.model_json_schema()


@dataclass(frozen=True)
class Excerpt:
    """One numbered piece of evidence, exactly as shown to the model."""

    evidence_id: str
    header: str
    text: str


@dataclass
class BatchResult:
    verdicts: dict[str, LlmFinding] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    # Rule 3 of section 5.7: the output could not be used, even after the retry.
    failed: bool = False
    rejected_quotes: int = 0
    errors: list[str] = field(default_factory=list)


def _neutralize(text: str) -> str:
    """An excerpt must not be able to close a delimiter and pose as instructions. A quote of
    this altered text fails verification, which is the right outcome for such a passage."""
    for tag in ("preuves", "extrait"):
        text = text.replace(f"</{tag}", f"<\\/{tag}").replace(f"<{tag}", f"<\\{tag}")
    return text


def excerpt_body(excerpt: Excerpt) -> str:
    return _neutralize(excerpt.text)


def build_user_prompt(
    req_key: str,
    old_text: str | None,
    new_text: str | None,
    facts: list[dict[str, Any]],
    excerpts: list[Excerpt],
) -> str:
    fact_lines = "\n".join(f"- {json.dumps(fact, ensure_ascii=False)}" for fact in facts)
    # A dedicated tag, because triple quotes also occur inside the Python code being quoted.
    blocks = "\n\n".join(
        f"[{excerpt.evidence_id}] {excerpt.header}\n<extrait>\n{excerpt_body(excerpt)}\n</extrait>"
        for excerpt in excerpts
    )
    return (
        f"Exigence {req_key}\n"
        f'Avant : "{old_text or "(absente)"}"\n'
        f'Après : "{new_text or "(supprimée)"}"\n'
        f"Faits détectés par le code :\n{fact_lines or '- aucun'}\n\n"
        f"<preuves>\n{blocks}\n</preuves>\n\n"
        "Pour chaque preuve, indique : impactée ou non, type d'impact, explication courte, "
        "passages cités, points incertains."
    )


def _quote_errors(finding: LlmFinding, excerpts: dict[str, Excerpt]) -> list[str]:
    errors = []
    if finding.verdict == "LIKELY_IMPACTED" and not finding.evidence:
        errors.append(f"{finding.candidate_id} : LIKELY_IMPACTED exige au moins une citation.")
    for item in finding.evidence:
        excerpt = excerpts.get(item.evidence_id)
        if excerpt is None:
            errors.append(f"{finding.candidate_id} : la preuve {item.evidence_id} n'existe pas.")
        elif not item.quote.strip() or item.quote.strip() not in excerpt_body(excerpt):
            errors.append(
                f"{finding.candidate_id} : la citation « {item.quote[:80]} » n'est pas "
                f"mot pour mot dans {item.evidence_id}."
            )
    return errors


def _parse(raw: str, excerpts: dict[str, Excerpt]) -> tuple[dict[str, LlmFinding], list[str], int]:
    """Returns (valid findings, errors for the retry message, number of rejected findings)."""
    try:
        output = LlmOutput.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        return {}, [f"JSON invalide : {first['msg']} à {first['loc']}."], 0

    valid: dict[str, LlmFinding] = {}
    answered: set[str] = set()
    errors: list[str] = []
    rejected = 0
    for finding in output.findings:
        if finding.candidate_id not in excerpts:
            errors.append(f"L'identifiant {finding.candidate_id} n'a pas été fourni.")
            rejected += 1
            continue
        answered.add(finding.candidate_id)
        quote_errors = _quote_errors(finding, excerpts)
        if quote_errors:
            errors += quote_errors
            rejected += 1
            continue
        valid.setdefault(finding.candidate_id, finding)
    errors += [f"Aucune réponse pour {eid}." for eid in excerpts if eid not in answered]
    return valid, errors, rejected


def analyze_batch(
    client: LLMClient,
    req_key: str,
    old_text: str | None,
    new_text: str | None,
    facts: list[dict[str, Any]],
    excerpts: list[Excerpt],
) -> BatchResult:
    """Raises LlmUnavailable if the model cannot be reached. Invalid output does not raise."""
    by_id = {excerpt.evidence_id: excerpt for excerpt in excerpts}
    user = build_user_prompt(req_key, old_text, new_text, facts, excerpts)
    result = BatchResult()
    schema = output_schema()

    for _attempt in range(2):  # one retry only (section 3, step 8)
        response = client.generate_json(SYSTEM_PROMPT, user, schema)
        result.attempts += 1
        result.input_tokens += response.input_tokens
        result.output_tokens += response.output_tokens
        valid, errors, rejected = _parse(response.text, by_id)
        # A verdict that was valid on the first attempt stays valid after the retry.
        result.verdicts = {**valid, **result.verdicts}
        result.rejected_quotes = rejected
        result.errors = errors
        if not errors:
            break
        user = (
            f"{build_user_prompt(req_key, old_text, new_text, facts, excerpts)}\n\n"
            "Ta réponse précédente a été refusée pour ces raisons :\n- "
            + "\n- ".join(errors[:8])
            + "\nRéponds de nouveau pour toutes les preuves, avec des citations exactes."
        )
    result.failed = not result.verdicts
    return result
