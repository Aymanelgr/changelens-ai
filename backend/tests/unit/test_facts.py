from decimal import Decimal

import pytest

from app.modules.requirements.facts import (
    compute_change_facts,
    extract_quantities,
    find_value_mentions,
    has_verifiable_fact,
    parse_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10000", "10000"),
        ("10 000", "10000"),
        ("10 000", "10000"),
        ("10 000", "10000"),
        ("10.000", "10000"),
        ("10,000", "10000"),
        ("1,5", "1.5"),
        ("2.75", "2.75"),
        ("1 000 000", "1000000"),
        ("10.000,50", "10000.50"),
        ("10,000.50", "10000.50"),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == Decimal(expected)


@pytest.mark.parametrize(
    ("text", "operator"),
    [
        ("supérieurs à 5 000 MAD", ">"),
        ("supérieur ou égal à 5 000 MAD", ">="),
        ("au moins 5 000 MAD", ">="),
        ("inférieurs à 5 000 MAD", "<"),
        ("inférieure ou égale à 5 000 MAD", "<="),
        ("au plus 5 000 MAD", "<="),
        ("au-delà de 5 000 MAD", ">"),
        ("dépassant 5 000 MAD", ">"),
        ("un surplus de 5 000 MAD", None),
        ("5 000 MAD", None),
    ],
)
def test_operator_wording(text, operator):
    assert extract_quantities(text)[0].operator == operator


def test_supporting_example_threshold_change():
    facts = compute_change_facts(
        "Les virements supérieurs à 10 000 MAD nécessitent une vérification supplémentaire.",
        "Les virements supérieurs à 5 000 MAD nécessitent une vérification supplémentaire.",
    )
    assert facts == [
        {
            "type": "THRESHOLD_CHANGED",
            "old": 10000,
            "new": 5000,
            "old_unit": "MAD",
            "unit": "MAD",
            "old_operator": ">",
            "operator": ">",
            "operator_changed": False,
        }
    ]


def test_operator_change_alone_is_detected():
    facts = compute_change_facts(
        "Les virements supérieurs à 5 000 MAD sont contrôlés.",
        "Les virements supérieurs ou égaux à 5 000 MAD sont contrôlés.",
    )
    assert facts[0]["type"] == "OPERATOR_CHANGED"
    assert (facts[0]["old_operator"], facts[0]["operator"]) == (">", ">=")


def test_vague_rewording_has_no_verifiable_fact():
    facts = compute_change_facts(
        "L'historique des virements est consultable.",
        "L'historique des virements doit être plus agréable à consulter.",
    )
    assert {f["type"] for f in facts} == {"MODALITY_CHANGED"}
    assert not has_verifiable_fact(facts)


def test_identifiers_are_not_quantities():
    assert extract_quantities("Voir REQ-012, la v2 et le lot ABC123.") == []


def test_quantity_added_and_removed():
    assert compute_change_facts("Sans limite.", "Limite de 3 tentatives.")[0] == {
        "type": "QUANTITY_ADDED",
        "new": 3,
        "unit": "tentative",
        "operator": None,
    }
    assert compute_change_facts("Limite de 3 tentatives.", "Sans limite.")[0]["type"] == (
        "QUANTITY_REMOVED"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Au-delà de 10 000 MAD",
        "plafond 10000 MAD",
        "plafond 10.000 MAD",
        "plafond 10,000 MAD",
        "plafond 10k MAD",
        "plafond 10 k",
        "plafond 10 000 dirhams",
    ],
)
def test_value_written_in_every_variant_is_found(text):
    assert len(find_value_mentions(text, 10000, "MAD")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "plafond 10 000 EUR",  # different unit: a different quantity
        "plafond 110000 MAD",  # 10000 inside a larger number
        "dossier REQ-10000",  # part of an identifier
        "plafond 1000 MAD",
    ],
)
def test_lookalikes_are_not_matched(text):
    assert find_value_mentions(text, 10000, "MAD") == []
