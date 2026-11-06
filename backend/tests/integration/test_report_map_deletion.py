"""FR-015 (report), FR-013 (impact map) and FR-018 (project deletion)."""

import shutil
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, engine
from app.modules.audit.models import AuditEvent
from tests.conftest import find_one, make_actor


def report(actor, run, **params):
    return actor.get(f"/analysis-runs/{run['id']}/report", params=params)


def test_fr015_markdown_contains_everything_the_specification_lists(demo):
    run = demo.run()
    response = report(demo.analyst, run)
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert 'filename="rapport-' in response.headers["content-disposition"]
    body = response.text
    assert "Seuil 10000 → 5000 MAD" in body
    assert "guide_utilisateur.md v1" in body
    assert "if amount > 10000:" in body
    assert "Au-delà de 10 000 MAD, une confirmation par SMS est demandée." in body
    assert "transfers/validation.py, ligne 42" in body
    assert "## Abstentions" in body and "REQ-031" in body
    assert "5 001 MAD" in body
    assert "getattr" in body or "non résolu" in body
    assert "## Limites connues" in body


def test_fr015_incomplete_review_is_stated(demo):
    body = report(demo.analyst, demo.run()).text
    assert "Revue incomplète" in body


def test_fr015_complete_review_is_not_called_incomplete(demo):
    run = demo.run()
    for finding in demo.findings(run):
        assert demo.decide(finding["id"]).status_code == 201
    body = report(demo.analyst, run).text
    assert "Revue incomplète" not in body
    assert "reviewer@test.local" in body


def test_fr015_ac2_no_test_is_presented_as_executed(demo):
    body = report(demo.analyst, demo.run()).text.lower()
    assert "suggested" in body
    assert "aucun de ces tests n'a été exécuté" in body
    for claim in ("réussi", "passed", "test exécuté"):
        assert claim not in body


def test_fr015_ac3_model_and_prompt_are_named(demo, install_ai):
    from tests.integration.conftest import FakeLlm, llm_finding

    install_ai(FakeLlm(lambda eid, header, text: llm_finding(eid, verdict="NOT_IMPACTED")))
    body = report(demo.analyst, demo.run()).text
    assert "fake-llm:1" in body
    assert "impact_analysis_v1+" in body


def test_fr015_without_llm_the_report_says_so(demo):
    body = report(demo.analyst, demo.run()).text
    assert "non analysé par le LLM" in body


def test_fr015_html_escapes_imported_content(demo):
    demo.analyst.upload(
        f"/projects/{demo.id}/documents",
        "piege.md",
        "## Piège\n\n<script>alert(1)</script> REQ-012 et 10 000 MAD\n",
    )
    response = report(demo.analyst, demo.run(), format="html")
    assert "text/html" in response.headers["content-type"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_fr015_export_is_audited_but_preview_is_not(demo):
    run = demo.run()
    report(demo.analyst, run, preview=True)
    actions = [e["action"] for e in demo.viewer.get(f"/projects/{demo.id}/audit-events").json()]
    assert "REPORT_EXPORTED" not in actions
    report(demo.reviewer, run, format="html")
    events = demo.viewer.get(f"/projects/{demo.id}/audit-events").json()
    exported = [e for e in events if e["action"] == "REPORT_EXPORTED"]
    assert exported[0]["details"]["format"] == "html"
    assert exported[0]["actor_email"] == "reviewer@test.local"


def test_fr015_viewer_cannot_export_and_outsider_sees_nothing(demo, client):
    run = demo.run()
    assert report(demo.viewer, run).status_code == 403
    outsider = make_actor(client, "out@test.local")
    assert report(outsider, run).status_code == 404


def test_fr015_requires_a_completed_run(demo):
    run = demo.run()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE analysis_run SET status = 'FAILED' WHERE id = :id"), {"id": run["id"]}
        )
        db.commit()
    assert report(demo.analyst, run).status_code == 409


def test_fr013_acceptance_turns_the_link_solid(demo):
    run = demo.run()
    validate = find_one(
        demo.findings(run, req_key="REQ-012"),
        target_contains="validate_transfer",
        impact="OUTDATED_VALUE",
    )
    base = f"/projects/{demo.id}/impact-map"
    before = demo.viewer.get(base, params={"change_request_id": demo.change_request}).json()
    link = next(item for item in before["links"] if item["finding_id"] == validate["id"])
    assert link["status"] == "SUGGESTED"
    kinds = {node["kind"] for node in before["nodes"]}
    assert {"requirement", "document", "code", "test"} <= kinds

    assert demo.decide(validate["id"]).status_code == 201
    after = demo.viewer.get(base, params={"req_key": "REQ-012"}).json()
    link = next(item for item in after["links"] if item["finding_id"] == validate["id"])
    assert link["status"] == "CONFIRMED"
    assert all(node["label"] != "REQ-031" for node in after["nodes"])


def test_fr013_rejected_link_leaves_the_map(demo):
    run = demo.run()
    findings = demo.findings(run, req_key="REQ-012")
    cache = find_one(findings, target_contains="limits.py")
    assert demo.decide(cache["id"], "REJECT", comment="Sans rapport.").status_code == 201
    body = demo.viewer.get(f"/projects/{demo.id}/impact-map").json()
    assert cache["id"] not in {item["finding_id"] for item in body["links"]}


def test_fr013_abstention_is_not_on_the_map(demo):
    run = demo.run()
    abstention = demo.findings(run, req_key="REQ-031")[0]
    body = demo.viewer.get(f"/projects/{demo.id}/impact-map").json()
    assert abstention["id"] not in {item["finding_id"] for item in body["links"]}


def test_fr013_outsider_gets_404(demo, client):
    outsider = make_actor(client, "out@test.local")
    assert outsider.get(f"/projects/{demo.id}/impact-map").status_code == 404


def pdf_with_threshold() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((50, 80), "Page de garde sans contenu utile ici.", fontsize=11)
    page = document.new_page()
    page.insert_text((50, 80), "3.2 Virements", fontsize=18)
    page.insert_text((50, 120), "Au-dela de 10 000 MAD, un SMS est demande.", fontsize=11)
    data = document.tobytes()
    document.close()
    return data


def test_fr002_pdf_citation_has_its_page_and_section(demo):
    response = demo.analyst.upload(
        f"/projects/{demo.id}/documents", "procedure.pdf", pdf_with_threshold()
    )
    assert response.status_code == 200
    assert response.json()["page_count"] == 2
    assert response.json()["extraction_status"] == "EXTRACTED"

    findings = demo.findings(demo.run(), req_key="REQ-012")
    pdf = find_one(findings, target_contains="procedure.pdf")
    evidence = pdf["evidence"][0]
    assert evidence["page"] == 2
    assert evidence["section_path"] == "3.2 Virements"
    assert evidence["quote"] == "Au-dela de 10 000 MAD, un SMS est demande."
    assert "page 2" in report(demo.analyst, demo.run()).text


def test_fr001_scanned_pdf_is_stored_as_needs_ocr_and_excluded(demo):
    document = pymupdf.open()
    document.new_page()
    scanned = document.tobytes()
    document.close()
    response = demo.analyst.upload(f"/projects/{demo.id}/documents", "scan.pdf", scanned)
    assert response.status_code == 200
    assert response.json()["extraction_status"] == "NEEDS_OCR"
    assert response.json()["segment_count"] == 0
    run = demo.run()
    assert len([i for i in run["inputs"] if i["input_type"] == "DOCUMENT_VERSION"]) == 4


def table_counts() -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table.name: connection.execute(
                select(table.c.id if "id" in table.c else text("1")).select_from(table)
            ).rowcount
            for table in Base.metadata.sorted_tables
        }


def test_fr018_deletion_removes_everything_except_a_minimal_event(demo, client):
    demo.decide(demo.findings(demo.run())[0]["id"])
    folder = Path(get_settings().storage_dir) / str(demo.id)
    assert folder.exists()

    # Another project must not be touched.
    other_admin = make_actor(client, "other@test.local")
    other = other_admin.post("/projects", json={"name": "Autre"}).json()["id"]

    response = demo.admin.client.delete(
        f"/projects/{demo.id}", params={"confirm": "Banque Démo"}, headers=demo.admin.headers
    )
    assert response.status_code == 204
    assert not folder.exists()

    counts = table_counts()
    for table in (
        "document",
        "document_version",
        "segment",
        "repository_snapshot",
        "code_symbol",
        "call_edge",
        "requirement_set",
        "requirement_version",
        "change_request",
        "analysis_run",
        "run_input",
        "finding",
        "finding_occurrence",
        "evidence",
        "test_suggestion",
        "review_decision",
        "trace_link",
    ):
        assert counts[table] == 0, table

    with SessionLocal() as db:
        remaining = db.scalars(select(AuditEvent).where(AuditEvent.project_id == demo.id)).all()
    assert [event.action for event in remaining] == ["PROJECT_DELETED"]
    assert remaining[0].actor_id is not None
    assert remaining[0].details == {"name": "Banque Démo"}

    assert demo.admin.get(f"/projects/{demo.id}").status_code == 404
    assert other_admin.get(f"/projects/{other}").status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(AuditEvent).where(AuditEvent.project_id == other)).first()


def test_fr018_only_a_project_admin_can_delete_with_the_right_name(demo, client):
    def attempt(actor, name):
        return actor.client.delete(
            f"/projects/{demo.id}", params={"confirm": name}, headers=actor.headers
        ).status_code

    assert attempt(demo.analyst, "Banque Démo") == 403
    assert attempt(demo.viewer, "Banque Démo") == 403
    assert attempt(make_actor(client, "out@test.local"), "Banque Démo") == 404
    assert attempt(demo.admin, "Mauvais nom") == 422
    assert demo.admin.get(f"/projects/{demo.id}").status_code == 200


def test_fr018_refused_while_an_analysis_is_running(demo):
    run = demo.run()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE analysis_run SET status = 'RUNNING' WHERE id = :id"), {"id": run["id"]}
        )
        db.commit()
    response = demo.admin.client.delete(
        f"/projects/{demo.id}", params={"confirm": "Banque Démo"}, headers=demo.admin.headers
    )
    assert response.status_code == 409


def test_fr016_purge_setting_cannot_be_used_on_another_project(demo):
    with SessionLocal() as db:
        db.execute(text("SELECT set_config('changelens.audit_purge_project', '999999', true)"))
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM audit_event WHERE project_id = :id"), {"id": demo.id})
        db.rollback()


def test_fr016_update_is_refused_even_with_the_purge_setting(demo):
    with SessionLocal() as db:
        db.execute(
            text("SELECT set_config('changelens.audit_purge_project', :id, true)"),
            {"id": str(demo.id)},
        )
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(
                text("UPDATE audit_event SET action = 'X' WHERE project_id = :id"), {"id": demo.id}
            )
        db.rollback()


def test_fr018_trace_is_visible_to_global_admins_only(demo, client):
    demo.admin.client.delete(
        f"/projects/{demo.id}", params={"confirm": "Banque Démo"}, headers=demo.admin.headers
    )
    assert demo.admin.get("/audit-events/project-deletions").status_code == 403
    root = make_actor(client, "root@test.local", is_admin=True)
    events = root.get("/audit-events/project-deletions").json()
    assert [e["details"]["name"] for e in events] == ["Banque Démo"]


@pytest.fixture(autouse=True)
def _no_leftover_folders():
    yield
    shutil.rmtree(Path(get_settings().storage_dir), ignore_errors=True)
