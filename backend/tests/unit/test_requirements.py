from types import SimpleNamespace

import pytest

from app.modules.requirements.boundary_tests import suggest_boundary_tests
from app.modules.requirements.diffing import diff_requirements, word_diff
from app.modules.requirements.parser import RequirementFileError, parse_requirements


def _req(key, text):
    return SimpleNamespace(req_key=key, title="", text=text)


def test_diff_classifies_all_four_statuses():
    changes = diff_requirements(
        [_req("REQ-1", "Même texte."), _req("REQ-2", "Ancien."), _req("REQ-3", "Retirée.")],
        [_req("REQ-1", "Même texte."), _req("REQ-2", "Nouveau."), _req("REQ-4", "Ajoutée.")],
    )
    assert {c.req_key: c.status for c in changes} == {
        "REQ-1": "UNCHANGED",
        "REQ-2": "MODIFIED",
        "REQ-3": "REMOVED",
        "REQ-4": "ADDED",
    }


def test_whitespace_alone_is_not_a_change():
    changes = diff_requirements(
        [_req("R-1", "Seuil  de\n5 000 MAD")], [_req("R-1", "Seuil de 5 000 MAD")]
    )
    # The non-breaking space is inside the number: normalization treats it as a space.
    assert changes[0].status == "UNCHANGED"


def test_word_diff_keeps_numbers_whole():
    parts = word_diff("supérieurs à 10 000 MAD", "supérieurs à 5 000 MAD")
    assert {"op": "removed", "text": "10 000"} in parts
    assert {"op": "added", "text": "5 000"} in parts


def test_yaml_duplicate_id_reports_its_line():
    content = "requirements:\n  - id: REQ-1\n    text: a\n  - id: REQ-1\n    text: b\n"
    with pytest.raises(RequirementFileError) as error:
        parse_requirements("r.yaml", content)
    assert "REQ-1" in str(error.value)
    assert error.value.line == 4


@pytest.mark.parametrize(
    "content",
    [
        "requirements:\n  - id: ''\n    text: a\n",
        "requirements:\n  - id: REQ-1\n    text: '  '\n",
        "requirements:\n  - id: nope\n    text: a\n",
        "requirements: 12\n",
        "- a\n- b\n",
        "requirements: [",
    ],
)
def test_invalid_yaml_files_are_refused(content):
    with pytest.raises(RequirementFileError):
        parse_requirements("r.yaml", content)


def test_yaml_cannot_instantiate_python_objects():
    content = "requirements:\n  - id: REQ-1\n    text: !!python/object/apply:os.system ['echo']\n"
    # The tagged value is never constructed: it is refused as an unusable text field.
    with pytest.raises(RequirementFileError):
        parse_requirements("r.yaml", content)


def test_markdown_format():
    parsed = parse_requirements(
        "r.md", "# Exigences\n\n## REQ-1 — Titre\n\nTexte un.\n\n## REQ-2\nTexte deux.\n"
    )
    assert [(r.key, r.title, r.text) for r in parsed] == [
        ("REQ-1", "Titre", "Texte un."),
        ("REQ-2", "", "Texte deux."),
    ]


REQUIREMENT = "Les virements supérieurs à 5 000 MAD nécessitent une vérification supplémentaire."


def _fact(operator):
    return {"type": "THRESHOLD_CHANGED", "new": 5000, "unit": "MAD", "operator": operator}


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (">", [False, False, True]),
        (">=", [False, True, True]),
        ("<", [True, False, False]),
        ("<=", [True, True, False]),
        ("==", [False, True, False]),
    ],
)
def test_boundary_results_depend_on_the_operator(operator, expected):
    tests = suggest_boundary_tests(_fact(operator), REQUIREMENT)
    assert [t["input"] for t in tests] == ["4 999 MAD", "5 000 MAD", "5 001 MAD"]
    assert [not t["expected"].startswith("pas de") for t in tests] == expected


def test_decimal_threshold_uses_a_small_step():
    tests = suggest_boundary_tests(
        {"type": "THRESHOLD_CHANGED", "new": 2.5, "unit": "%", "operator": ">"}, "Frais."
    )
    assert [t["input"] for t in tests] == ["2.49 %", "2.5 %", "2.51 %"]


def test_no_operator_means_no_boundary_guess():
    assert suggest_boundary_tests(_fact(None), REQUIREMENT) == []
