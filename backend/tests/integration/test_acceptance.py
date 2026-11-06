"""The specification's global acceptance scenarios (section 6.12), through the real API."""

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.enums import TargetType
from app.modules.analysis.models import Evidence
from app.modules.code_analysis.models import CodeSymbol
from app.modules.ingestion.models import Segment
from tests.conftest import DEMO_DATA, demo_app_files, find_one, make_actor, zip_bytes


def test_ac01_threshold_change_is_detected(demo):
    diff = demo.viewer.get(f"/change-requests/{demo.change_request}/diff").json()
    by_key = {item["req_key"]: item for item in diff}
    assert {k: v["status"] for k, v in by_key.items()} == {
        "REQ-012": "MODIFIED",
        "REQ-020": "UNCHANGED",
        "REQ-031": "MODIFIED",
        "REQ-040": "REMOVED",
        "REQ-045": "ADDED",
    }
    fact = by_key["REQ-012"]["facts"][0]
    assert fact["type"] == "THRESHOLD_CHANGED"
    assert (fact["old"], fact["new"], fact["unit"]) == (10000, 5000, "MAD")
    assert (fact["operator"], fact["operator_changed"]) == (">", False)


def test_ac02_outdated_documentation_is_cited_exactly(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    guide = find_one(findings, target_contains="3.2 Virements")
    assert guide["impact_type"] == "OUTDATED_VALUE"
    assert guide["link_type"] == "STATIC"
    assert guide["support"] == "STRONG"
    quote = guide["evidence"][0]
    assert quote["quote"] == "Au-delà de 10 000 MAD, une confirmation par SMS est demandée."
    assert quote["document_name"] == "guide_utilisateur.md"
    assert "3.2 Virements" in quote["section_path"]


def test_ac02_value_is_found_when_written_differently(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    policy = find_one(findings, target_contains="politique_securite.txt")
    assert "10.000 MAD" in policy["evidence"][0]["quote"]


def test_ac03_code_candidate_and_neighbour(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")

    validate = find_one(findings, target_contains="validate_transfer", impact="OUTDATED_VALUE")
    assert validate["support"] == "STRONG"
    assert validate["target"]["file"] == "transfers/validation.py"
    assert validate["evidence"][0]["quote"] == "if amount > 10000:"
    assert validate["evidence"][0]["start_line"] == 42

    neighbour = find_one(findings, target_contains="require_strong_auth")
    assert neighbour["impact_type"] == "DEPENDENT_COMPONENT"
    assert neighbour["support"] == "MODERATE"
    assert neighbour["evidence"][0]["quote"] == "require_strong_auth(account)"


def test_ac04_boundary_tests(demo):
    run = demo.run()
    tests = demo.analyst.get(f"/analysis-runs/{run['id']}/test-suggestions").json()
    assert [(t["input_value"], t["expected"]) for t in tests] == [
        ("4 999 MAD", "pas de vérification supplémentaire"),
        ("5 000 MAD", "pas de vérification supplémentaire"),
        ("5 001 MAD", "vérification supplémentaire"),
    ]
    assert {t["status"] for t in tests} == {"SUGGESTED"}


def test_ac05_operator_inconsistency(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    finding = find_one(findings, target_contains="flag_large_transfer", impact="INCONSISTENCY")
    roles = {e["role"] for e in finding["evidence"]}
    assert roles == {"SUPPORTS", "CONTRADICTS"}
    contradicting = next(e for e in finding["evidence"] if e["role"] == "CONTRADICTS")
    assert contradicting["quote"] == "if amount >= 10000:"
    # The code compares with 10000, so the citation must be the text stating 10 000.
    supporting = next(e for e in finding["evidence"] if e["role"] == "SUPPORTS")
    assert "10 000 MAD" in supporting["quote"]
    assert "ancienne version" in finding["explanation"]
    # Contradictions go first in the review queue.
    assert findings[0]["impact_type"] == "INCONSISTENCY"


def test_ac05_new_value_with_wrong_operator_cites_the_new_text(demo):
    changed = demo_app_files()
    changed["reporting/alerts.py"] = changed["reporting/alerts.py"].replace(
        "amount >= 10000", "amount >= 5000"
    )
    demo.analyst.upload(f"/projects/{demo.id}/repository-snapshots", "v2.zip", zip_bytes(changed))
    findings = demo.findings(demo.run(), req_key="REQ-012")
    finding = find_one(findings, target_contains="flag_large_transfer", impact="INCONSISTENCY")
    supporting = next(e for e in finding["evidence"] if e["role"] == "SUPPORTS")
    assert "5 000 MAD" in supporting["quote"]
    assert "nouvelle version" in finding["explanation"]


def test_ac05_complementary_operator_is_not_flagged(client):
    """`if amount <= 10000: skip` states the same rule as `> 10000 triggers`."""
    admin = make_actor(client, "a@test.local")
    project = admin.post("/projects", json={"name": "P"}).json()["id"]
    code = "def skip_check(amount):\n    if amount <= 10000:\n        return True\n"
    admin.upload(f"/projects/{project}/repository-snapshots", "r.zip", zip_bytes({"m.py": code}))
    requirements = DEMO_DATA / "requirements"
    ids = [
        admin.upload(
            f"/projects/{project}/requirement-sets", p, (requirements / p).read_bytes()
        ).json()["id"]
        for p in ("requirements_v1.yaml", "requirements_v2.yaml")
    ]
    change = admin.post(
        f"/projects/{project}/change-requests",
        json={"title": "c", "set_before_id": ids[0], "set_after_id": ids[1]},
    ).json()["id"]
    run_id = admin.post(f"/change-requests/{change}/analysis-runs", json={}).json()["id"]
    findings = admin.get(f"/analysis-runs/{run_id}/findings").json()
    assert "INCONSISTENCY" not in {f["impact_type"] for f in findings}
    assert "OUTDATED_VALUE" in {f["impact_type"] for f in findings}


def test_ac06_vague_requirement_produces_an_abstention(demo):
    findings = demo.findings(demo.run(), req_key="REQ-031")
    assert len(findings) == 1
    assert findings[0]["link_type"] == "INSUFFICIENT_EVIDENCE"
    assert findings[0]["target_type"] == "REQUIREMENT"
    assert "précisez le critère" in findings[0]["explanation"]


def test_ac07_review_confirms_the_link_and_audits_it(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    validate = find_one(findings, target_contains="validate_transfer", impact="OUTDATED_VALUE")

    response = demo.decide(validate["id"], comment="Seuil à mettre à jour dans TRF-77.")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACCEPTED"
    assert body["trace_link"]["status"] == "CONFIRMED"

    events = demo.viewer.get(f"/projects/{demo.id}/audit-events").json()
    decided = [e for e in events if e["action"] == "FINDING_DECIDED"]
    assert len(decided) == 1
    assert decided[0]["entity_id"] == validate["id"]
    assert decided[0]["details"]["decision"] == "ACCEPT"
    assert decided[0]["actor_id"] is not None
    assert decided[0]["created_at"]


def test_ac08_reanalysis_creates_no_duplicates_and_keeps_decisions(demo):
    first = demo.run()
    findings = demo.findings(first, req_key="REQ-012")
    guide = find_one(findings, target_contains="3.2 Virements")
    policy = find_one(findings, target_contains="politique_securite.txt")
    # The policy finding is decided. The guide finding is still PROPOSED.
    assert demo.decide(policy["id"]).status_code == 201

    corrected = (DEMO_DATA / "docs-corrected" / "guide_utilisateur.md").read_bytes()
    response = demo.analyst.upload(
        f"/projects/{demo.id}/documents", "guide_utilisateur.md", corrected
    )
    assert response.json()["number"] == 2

    second = demo.run()
    after = demo.findings(second, req_key="REQ-012")
    keys = [(f["target_key"], f["impact_type"]) for f in after]
    assert len(keys) == len(set(keys)), "a duplicate finding was created"

    # No longer produced: the guide no longer holds the old value in section 3.2.
    assert not [f for f in after if "3.2 Virements" in f["target_key"]]
    old_view = {f["id"]: f for f in demo.findings(first, req_key="REQ-012")}
    assert old_view[guide["id"]]["status"] == "SUPERSEDED"

    # Reproduced with identical evidence: the decision is untouched.
    same_policy = find_one(after, target_contains="politique_securite.txt")
    assert same_policy["id"] == policy["id"]
    assert same_policy["status"] == "ACCEPTED"


def test_ac08_rerun_without_changes_changes_nothing(demo):
    first = demo.findings(demo.run())
    second = demo.findings(demo.run())
    assert sorted(f["id"] for f in first) == sorted(f["id"] for f in second)


def test_ac08_changed_evidence_reopens_a_decided_finding(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    validate = find_one(findings, target_contains="validate_transfer", impact="OUTDATED_VALUE")
    assert demo.decide(validate["id"]).status_code == 201

    changed = demo_app_files()
    changed["transfers/validation.py"] = changed["transfers/validation.py"].replace(
        "if amount > 10000:", "if amount > 10000 and account.segment != 'vip':"
    )
    response = demo.analyst.upload(
        f"/projects/{demo.id}/repository-snapshots", "v2.zip", zip_bytes(changed)
    )
    assert response.status_code == 200

    # Until the next analysis, the old finding is flagged rather than deleted.
    before = demo.findings(
        demo.analyst.get(f"/change-requests/{demo.change_request}/analysis-runs").json()[0],
        req_key="REQ-012",
    )
    assert find_one(before, target_contains="validate_transfer", impact="OUTDATED_VALUE")["stale"]

    after = demo.findings(demo.run(), req_key="REQ-012")
    reopened = find_one(after, target_contains="validate_transfer", impact="OUTDATED_VALUE")
    assert reopened["id"] == validate["id"]
    assert reopened["status"] == "PROPOSED"
    assert reopened["evidence_changed"]
    assert not reopened["stale"]

    history = demo.viewer.get(f"/findings/{validate['id']}/decisions").json()
    assert [d["decision"] for d in history] == ["ACCEPT"]  # the old decision is kept

    events = demo.viewer.get(f"/projects/{demo.id}/audit-events").json()
    assert "FINDING_REOPENED" in {e["action"] for e in events}


def test_finished_run_keeps_what_it_saw(demo):
    """Gap 1: the old run still shows its own evidence after a later run saw something else."""
    first = demo.run()
    validate = find_one(
        demo.findings(first, req_key="REQ-012"),
        target_contains="validate_transfer",
        impact="OUTDATED_VALUE",
    )
    changed = demo_app_files()
    changed["transfers/validation.py"] = changed["transfers/validation.py"].replace(
        "if amount > 10000:", "if 10000 < amount:"
    )
    demo.analyst.upload(f"/projects/{demo.id}/repository-snapshots", "v2.zip", zip_bytes(changed))
    second = demo.run()

    old = find_one(
        demo.findings(first), target_contains="validate_transfer", impact="OUTDATED_VALUE"
    )
    new = find_one(
        demo.findings(second), target_contains="validate_transfer", impact="OUTDATED_VALUE"
    )
    assert old["id"] == new["id"] == validate["id"]
    assert old["evidence"][0]["quote"] == "if amount > 10000:"
    assert new["evidence"][0]["quote"] == "if 10000 < amount:"


def test_later_change_request_does_not_inherit_decisions(demo):
    """The fingerprint gap: REQ-012 changing again is a new question for the reviewer."""
    run = demo.run()
    validate = find_one(
        demo.findings(run, req_key="REQ-012"),
        target_contains="validate_transfer",
        impact="OUTDATED_VALUE",
    )
    assert demo.decide(validate["id"]).status_code == 201

    other = demo.analyst.post(
        f"/projects/{demo.id}/change-requests",
        json={"title": "Encore", "set_before_id": demo.set_v1, "set_after_id": demo.set_v2},
    ).json()["id"]
    run_id = demo.analyst.post(f"/change-requests/{other}/analysis-runs", json={}).json()["id"]
    findings = demo.analyst.get(f"/analysis-runs/{run_id}/findings").json()
    again = find_one(findings, target_contains="validate_transfer", impact="OUTDATED_VALUE")
    assert again["id"] != validate["id"]
    assert again["status"] == "PROPOSED"


def test_ac10_core_scenarios_work_without_any_llm(demo):
    """With the LLM off, every finding says so honestly, and AC-01, 02 and 04 still hold."""
    run = demo.run()
    assert run["llm_model"] is None
    assert run["summary"]["llm"]["status"] == "DISABLED"
    assert run["summary"]["llm"]["label"] == "non analysé par le LLM"
    assert not any(f["llm_analyzed"] for f in demo.findings(run))


def test_explicit_links_from_comment_test_name_and_document(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    comment = find_one(findings, target_contains="notify_extra_verification")
    assert (comment["link_type"], comment["impact_type"]) == ("EXPLICIT", "LOGIC_REVIEW")

    test = find_one(findings, target_contains="test_req_012_at_threshold")
    assert (test["link_type"], test["impact_type"]) == ("EXPLICIT", "TEST_UPDATE")

    spec = find_one(findings, target_contains="specification_fonctionnelle.md")
    assert (spec["link_type"], spec["impact_type"]) == ("EXPLICIT", "DOC_UPDATE")


def test_false_friends_are_proposed_at_moderate_support_only(demo):
    findings = demo.findings(demo.run(), req_key="REQ-012")
    cache = find_one(findings, target_contains="limits.py")
    assert cache["support"] == "MODERATE"
    clients = find_one(findings, target_contains="1. Présentation")
    assert clients["support"] == "MODERATE"


def test_nfr004_every_quote_is_literally_in_its_source(demo):
    run = demo.run()
    findings = demo.findings(run)
    assert findings
    assert all(f["evidence"] for f in findings), "OBJ-4: every finding has a citation"
    with SessionLocal() as db:
        for evidence in db.scalars(select(Evidence)):
            if evidence.segment_id:
                assert evidence.quote in db.get(Segment, evidence.segment_id).text
            elif evidence.symbol_id:
                assert evidence.quote in db.get(CodeSymbol, evidence.symbol_id).source_text
    assert run["summary"]["evidence_rejected"] == 0


def test_run_records_exact_inputs(demo):
    run = demo.run()
    types = [i["input_type"] for i in run["inputs"]]
    assert types.count("DOCUMENT_VERSION") == 4
    assert types.count("REPOSITORY_SNAPSHOT") == 1
    assert "REQUIREMENT_SET_BEFORE" in types
    assert "REQUIREMENT_SET_AFTER" in types


def test_abstention_creates_no_trace_link(demo):
    run = demo.run()
    finding = demo.findings(run, req_key="REQ-031")[0]
    assert finding["target_type"] == TargetType.REQUIREMENT
    assert demo.decide(finding["id"]).json()["trace_link"] is None


def test_demo_lifecycle_reaches_review_completed_then_closed(demo):
    run = demo.run()
    status = demo.analyst.get(f"/change-requests/{demo.change_request}").json()["status"]
    assert status == "IN_REVIEW"
    for finding in demo.findings(run):
        last = demo.decide(finding["id"])
        assert last.status_code == 201, last.text
    assert last.json()["change_request_status"] == "REVIEW_COMPLETED"
    closed = demo.analyst.post(f"/change-requests/{demo.change_request}/close")
    assert closed.json()["status"] == "CLOSED"
    assert (
        demo.analyst.post(
            f"/change-requests/{demo.change_request}/analysis-runs", json={}
        ).status_code
        == 409
    )
