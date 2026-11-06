"""Boundary test suggestions (pipeline step 9). Deterministic and never executed (AI-007)."""

from decimal import Decimal
from typing import Any

from app.modules.requirements.facts import extract_consequence

_APPLIES = {
    ">": lambda x, s: x > s,
    ">=": lambda x, s: x >= s,
    "<": lambda x, s: x < s,
    "<=": lambda x, s: x <= s,
    "==": lambda x, s: x == s,
}


def _format(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}".replace(",", " ")
    return str(value)


def suggest_boundary_tests(fact: dict[str, Any], requirement_text: str) -> list[dict[str, str]]:
    """For threshold S, test S - step, S and S + step. Whether S itself triggers the
    rule depends on the operator: that is the whole point of a boundary test."""
    if fact["type"] not in {"THRESHOLD_CHANGED", "OPERATOR_CHANGED"}:
        return []
    operator = fact.get("operator")
    if operator not in _APPLIES:
        return []

    threshold = Decimal(str(fact["new"]))
    step = Decimal(1) if threshold == threshold.to_integral_value() else Decimal("0.01")
    unit = f" {fact['unit']}" if fact.get("unit") else ""
    consequence = extract_consequence(requirement_text)
    applies_text = consequence or "la règle s'applique"
    absent_text = f"pas de {consequence}" if consequence else "la règle ne s'applique pas"

    suggestions = []
    positions = (
        (threshold - step, "Juste sous la borne"),
        (threshold, "Exactement à la borne"),
        (threshold + step, "Juste au-dessus de la borne"),
    )
    condition = f"valeur {operator} {_format(threshold)}{unit}"
    for value, position in positions:
        suggestions.append(
            {
                "input": f"{_format(value)}{unit}",
                "expected": applies_text if _APPLIES[operator](value, threshold) else absent_text,
                "rationale": f"{position}, condition : {condition}.",
            }
        )
    return suggestions
