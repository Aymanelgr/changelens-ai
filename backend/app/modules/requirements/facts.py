"""Deterministic extraction of facts from French requirement text (pipeline step 3).

No LLM here: a threshold change must be detected the same way every time.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Numbers: "10 000", "10.000", "10,000", "10000", "1,5", "10k"
# ---------------------------------------------------------------------------
_SPACES = "   "
NUMBER_PATTERN = rf"\d{{1,3}}(?:[{_SPACES}.,]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"

# The lookarounds keep identifiers out: REQ-012, v2, ABC123 and 12a are not quantities.
_NUMBER_RE = re.compile(rf"(?<![\w-])(?P<num>{NUMBER_PATTERN})(?P<k>\s?[kK](?!\w))?(?![\w-])")

_UNITS = {
    "mad": "MAD",
    "dh": "MAD",
    "dhs": "MAD",
    "dirham": "MAD",
    "dirhams": "MAD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "€": "EUR",
    "usd": "USD",
    "$": "USD",
    "%": "%",
    "jour": "jour",
    "jours": "jour",
    "heure": "heure",
    "heures": "heure",
    "minute": "minute",
    "minutes": "minute",
    "seconde": "seconde",
    "secondes": "seconde",
    "mois": "mois",
    "an": "an",
    "ans": "an",
    "tentative": "tentative",
    "tentatives": "tentative",
    "caractère": "caractère",
    "caractères": "caractère",
}
_UNIT_RE = re.compile(r"\s*(?P<unit>%|€|\$|[A-Za-zÀ-ÿ]+)", re.IGNORECASE)

_EQUAL = r"[ée]ga(?:le?s?|ux)"  # égal, égale, égales, égaux

# Longest wording first: "supérieur ou égal à" must win over "supérieur".
_OPERATORS: list[tuple[str, str]] = [
    (rf"sup[ée]rieure?s?\s+ou\s+{_EQUAL}\s+[àa]", ">="),
    (rf"inf[ée]rieure?s?\s+ou\s+{_EQUAL}\s+[àa]", "<="),
    (r"sup[ée]rieure?s?\s+[àa]", ">"),
    (r"inf[ée]rieure?s?\s+[àa]", "<"),
    (rf"{_EQUAL}\s+[àa]", "=="),
    (r"au\s+moins", ">="),
    (r"au\s+plus", "<="),
    (r"au[-\s]del[àa]\s+de", ">"),
    (r"plus\s+de", ">"),
    (r"moins\s+de", "<"),
    (r"d[ée]passant", ">"),
    (r"exc[ée]dant", ">"),
    (r"minimum\s+de", ">="),
    (r"maximum\s+de", "<="),
]
_OPERATOR_RE = re.compile(
    r"\b(?:" + "|".join(f"(?:{p})" for p, _ in _OPERATORS) + r")\s*$", re.IGNORECASE
)

_MODALITIES = [
    (re.compile(r"\bne\s+(?:doit|doivent|peut|peuvent)\s+pas\b|\binterdit", re.I), "MUST_NOT"),
    (re.compile(r"\b(?:doit|doivent)\b|\bobligatoire", re.I), "MUST"),
    (re.compile(r"\b(?:peut|peuvent)\b", re.I), "MAY"),
]

# What a rule triggers, used to write readable expected results for boundary tests.
_CONSEQUENCE_RE = re.compile(
    r"\b(?:n[ée]cessitent?|requi[eè]rent?|d[ée]clenchent?|imposent?)\s+"
    r"(?:une?\s+|des\s+|la\s+|le\s+|les\s+)?(?P<what>[^.;,]+)",
    re.IGNORECASE,
)

# Facts that can be checked against code and documents.
VERIFIABLE_FACT_TYPES = {
    "THRESHOLD_CHANGED",
    "OPERATOR_CHANGED",
    "UNIT_CHANGED",
    "QUANTITY_ADDED",
    "QUANTITY_REMOVED",
}


def parse_number(raw: str) -> Decimal | None:
    """Read a French or international number. A lone separator followed by exactly
    three digits is a thousands separator; otherwise it marks decimals."""
    cleaned = raw
    for space in _SPACES:
        cleaned = cleaned.replace(space, "")
    separators = [c for c in cleaned if c in ".,"]
    if len(separators) >= 2:
        if len(set(separators)) == 2:  # "10.000,50": the last separator marks decimals
            decimal_sep = separators[-1]
            thousands_sep = "." if decimal_sep == "," else ","
            cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
        else:
            cleaned = cleaned.replace(separators[0], "")
    elif separators:
        integer, fraction = cleaned.split(separators[0])
        if len(fraction) == 3 and 1 <= len(integer) <= 3:
            cleaned = integer + fraction
        else:
            cleaned = f"{integer}.{fraction}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def to_json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _read_unit(text: str, end: int) -> str | None:
    match = _UNIT_RE.match(text, end)
    if match:
        return _UNITS.get(match.group("unit").lower())
    return None


@dataclass(frozen=True)
class NumberMention:
    start: int
    end: int
    raw: str
    value: Decimal
    unit: str | None


def find_numbers(text: str) -> list[NumberMention]:
    mentions = []
    for match in _NUMBER_RE.finditer(text):
        value = parse_number(match.group("num"))
        if value is None:
            continue
        if match.group("k"):
            value *= 1000
        mentions.append(
            NumberMention(
                match.start(), match.end(), match.group(0), value, _read_unit(text, match.end())
            )
        )
    return mentions


def find_value_mentions(text: str, value: int | float, unit: str | None) -> list[NumberMention]:
    """Every way `value` is written in `text`. A mention carrying a different unit
    ("10 000 EUR" when looking for MAD) is a different quantity, so it is skipped."""
    target = Decimal(str(value))
    return [
        m
        for m in find_numbers(text)
        if m.value == target and (m.unit is None or unit is None or m.unit == unit)
    ]


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str | None
    operator: str | None


def _operator_before(text: str, position: int) -> str | None:
    match = _OPERATOR_RE.search(text[:position])
    if not match:
        return None
    wording = match.group(0).strip()
    for pattern, symbol in _OPERATORS:
        if re.fullmatch(pattern, wording, re.IGNORECASE):
            return symbol
    return None


def extract_quantities(text: str) -> list[Quantity]:
    return [Quantity(m.value, m.unit, _operator_before(text, m.start)) for m in find_numbers(text)]


def extract_modality(text: str) -> str | None:
    for pattern, name in _MODALITIES:
        if pattern.search(text):
            return name
    return None


def extract_consequence(text: str) -> str | None:
    match = _CONSEQUENCE_RE.search(text)
    return match.group("what").strip() if match else None


def _paired_fact(old: Quantity, new: Quantity) -> dict[str, Any]:
    if old.value != new.value:
        fact_type = "THRESHOLD_CHANGED"
    elif old.unit != new.unit:
        fact_type = "UNIT_CHANGED"
    else:
        fact_type = "OPERATOR_CHANGED"
    return {
        "type": fact_type,
        "old": to_json_number(old.value),
        "new": to_json_number(new.value),
        "old_unit": old.unit,
        "unit": new.unit,
        "old_operator": old.operator,
        "operator": new.operator,
        "operator_changed": old.operator != new.operator,
    }


def compute_change_facts(old_text: str, new_text: str) -> list[dict[str, Any]]:
    """Compare two versions of a requirement and describe what changed."""
    old_quantities = extract_quantities(old_text)
    new_quantities = extract_quantities(new_text)

    # Quantities present on both sides did not change, wherever they appear.
    only_old = [q for q in old_quantities if q not in new_quantities]
    only_new = [q for q in new_quantities if q not in old_quantities]

    facts = [_paired_fact(old, new) for old, new in zip(only_old, only_new, strict=False)]
    paired = min(len(only_old), len(only_new))
    for quantity in only_old[paired:]:
        facts.append(
            {
                "type": "QUANTITY_REMOVED",
                "old": to_json_number(quantity.value),
                "old_unit": quantity.unit,
                "old_operator": quantity.operator,
            }
        )
    for quantity in only_new[paired:]:
        facts.append(
            {
                "type": "QUANTITY_ADDED",
                "new": to_json_number(quantity.value),
                "unit": quantity.unit,
                "operator": quantity.operator,
            }
        )

    old_modality, new_modality = extract_modality(old_text), extract_modality(new_text)
    if old_modality != new_modality:
        facts.append({"type": "MODALITY_CHANGED", "old": old_modality, "new": new_modality})

    if not facts:
        facts.append({"type": "TEXT_CHANGED"})
    return facts


def has_verifiable_fact(facts: list[dict[str, Any]]) -> bool:
    return any(fact["type"] in VERIFIABLE_FACT_TYPES for fact in facts)
