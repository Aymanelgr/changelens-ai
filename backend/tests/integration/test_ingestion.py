"""Acceptance criteria of FR-001, FR-004 and FR-008."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.modules.analysis import pipeline
from app.modules.analysis.models import AnalysisRun
from tests.conftest import demo_app_files, find_one, make_actor, zip_bytes


@pytest.fixture
def analyst(client):
    return make_actor(client, "a@test.local")


@pytest.fixture
def project(analyst):
    return analyst.post("/projects", json={"name": "P"}).json()["id"]


def test_fr001_markdown_creates_version_and_segments(analyst, project):
    response = analyst.upload(f"/projects/{project}/documents", "a.md", "# T\n\nTexte.\n")
    assert response.status_code == 200
    body = response.json()
    assert body["number"] == 1
    assert body["segment_count"] >= 1


def test_fr001_identical_second_import_creates_no_version(analyst, project):
    url = f"/projects/{project}/documents"
    first = analyst.upload(url, "a.md", "# T\nTexte.\n").json()
    second = analyst.upload(url, "a.md", "# T\nTexte.\n").json()
    assert second["already_imported"]
    assert second["version_id"] == first["version_id"]
    assert second["message"] == "Déjà importé."
    assert len(analyst.get(url).json()) == 1


def test_fr001_changed_content_creates_a_new_version(analyst, project):
    url = f"/projects/{project}/documents"
    analyst.upload(url, "a.md", "# T\nUn.\n")
    assert analyst.upload(url, "a.md", "# T\nDeux.\n").json()["number"] == 2
    assert [d["number"] for d in analyst.get(url).json()] == [1, 2]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("virus.pdf", b"MZ\x90\x00 an exe renamed to pdf"),  # FR-001 criterion 2
        ("virus.md", b"MZ\x90\x00 an exe renamed to md"),
        ("virus.txt", b"\x7fELF binary"),
        ("a.exe", b"hello"),
        ("no_extension", b"hello"),
        ("latin1.txt", "é".encode("latin-1")),
        ("empty.md", b"   \n"),
    ],
)
def test_fr001_bad_files_are_refused_with_422(analyst, project, name, content):
    response = analyst.upload(f"/projects/{project}/documents", name, content)
    assert response.status_code == 422
    assert response.json()["detail"]


def test_fr001_damaged_pdf_is_refused_with_a_clear_message(analyst, project):
    response = analyst.upload(f"/projects/{project}/documents", "a.pdf", b"%PDF-1.7 ...")
    assert response.status_code == 422
    assert "illisible" in response.json()["detail"]


def test_fr001_import_is_audited(analyst, project):
    analyst.upload(f"/projects/{project}/documents", "a.md", "# T\nTexte.\n")
    events = analyst.get(f"/projects/{project}/audit-events").json()
    assert "DOCUMENT_UPLOADED" in [e["action"] for e in events]


def test_fr004_ten_requirements(analyst, project):
    lines = ["requirements:"]
    for i in range(10):
        lines += [f"  - id: REQ-{i:03}", f"    text: Texte {i}"]
    response = analyst.upload(f"/projects/{project}/requirement-sets", "r.yaml", "\n".join(lines))
    assert response.status_code == 200
    assert response.json()["requirement_count"] == 10


def test_fr004_duplicate_id_is_refused_with_its_line(analyst, project):
    content = "requirements:\n  - id: REQ-1\n    text: a\n  - id: REQ-1\n    text: b\n"
    response = analyst.upload(f"/projects/{project}/requirement-sets", "r.yaml", content)
    assert response.status_code == 422
    assert "Ligne 4" in response.json()["detail"]


def test_fr004_previous_set_stays_readable(analyst, project):
    url = f"/projects/{project}/requirement-sets"
    v1 = analyst.upload(url, "r.yaml", "requirements:\n  - id: REQ-1\n    text: Un\n").json()["id"]
    analyst.upload(url, "r.yaml", "requirements:\n  - id: REQ-1\n    text: Deux\n")
    assert analyst.get(f"/requirement-sets/{v1}/requirements").json()[0]["text"] == "Un"


def test_snapshot_reports_unresolved_calls_and_exclusions(analyst, project):
    files = demo_app_files()
    files[".env"] = "SECRET=1"
    response = analyst.upload(
        f"/projects/{project}/repository-snapshots", "r.zip", zip_bytes(files)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unresolved_call_count"] >= 2  # getattr(...) and handler(data)
    assert body["excluded_sensitive_count"] == 1
    assert any(".env" in w for w in body["warnings"])


def test_snapshot_with_syntax_error_still_imports_the_rest(analyst, project):
    response = analyst.upload(
        f"/projects/{project}/repository-snapshots",
        "r.zip",
        zip_bytes({"bad.py": "def (:", "good.py": "def f():\n    pass\n"}),
    )
    assert response.status_code == 200
    assert response.json()["parse_error_count"] == 1


def test_dangerous_archive_is_refused(analyst, project):
    response = analyst.upload(
        f"/projects/{project}/repository-snapshots", "r.zip", zip_bytes({"../evil.py": "x"})
    )
    assert response.status_code == 422


def test_analysis_needs_at_least_one_source(analyst, project):
    url = f"/projects/{project}/requirement-sets"
    a = analyst.upload(url, "a.yaml", "requirements:\n  - id: R-1\n    text: Un\n").json()["id"]
    b = analyst.upload(url, "b.yaml", "requirements:\n  - id: R-1\n    text: Deux\n").json()["id"]
    change = analyst.post(
        f"/projects/{project}/change-requests",
        json={"title": "c", "set_before_id": a, "set_after_id": b},
    ).json()["id"]
    assert analyst.post(f"/change-requests/{change}/analysis-runs", json={}).status_code == 422


def test_fr008_second_concurrent_run_gets_409(demo, monkeypatch):
    # Keep the first run "active" by not letting its background task do anything.
    monkeypatch.setattr("app.modules.analysis.router.run_analysis", lambda _run_id: None)
    url = f"/change-requests/{demo.change_request}/analysis-runs"
    assert demo.analyst.post(url, json={}).status_code == 202
    assert demo.analyst.post(url, json={}).status_code == 409


def test_database_itself_refuses_two_active_runs(demo, monkeypatch):
    """The index is the real guarantee: it holds even if two requests pass the status check."""
    monkeypatch.setattr("app.modules.analysis.router.run_analysis", lambda _run_id: None)
    demo.analyst.post(f"/change-requests/{demo.change_request}/analysis-runs", json={})
    with SessionLocal() as db:
        run = db.scalar(select(AnalysisRun))
        db.add(
            AnalysisRun(
                change_request_id=run.change_request_id,
                cr_status_before=run.cr_status_before,
                created_by=run.created_by,
            )
        )
        with pytest.raises(Exception, match="uq_analysis_run_one_active"):
            db.flush()


def _boom(*_args):
    raise RuntimeError("simulated crash")


def test_failed_first_run_returns_to_draft(demo, monkeypatch):
    monkeypatch.setattr(pipeline, "_execute", _boom)
    run_id = demo.analyst.post(
        f"/change-requests/{demo.change_request}/analysis-runs", json={}
    ).json()["id"]
    run = demo.analyst.get(f"/analysis-runs/{run_id}").json()
    assert run["status"] == "FAILED"
    assert "simulated" not in run["failure_reason"]  # internals are logged, not shown
    status = demo.analyst.get(f"/change-requests/{demo.change_request}").json()["status"]
    assert status == "DRAFT"


def test_failed_reanalysis_returns_to_review_not_draft(demo, monkeypatch):
    """Gap 3: reviewed work must not be thrown back to DRAFT by a failed re-analysis."""
    run = demo.run()
    find_one(demo.findings(run), target_contains="3.2 Virements")
    monkeypatch.setattr(pipeline, "_execute", _boom)
    demo.analyst.post(f"/change-requests/{demo.change_request}/analysis-runs", json={})
    status = demo.analyst.get(f"/change-requests/{demo.change_request}").json()["status"]
    assert status == "IN_REVIEW"


def test_reanalysis_is_allowed_after_review_completed(demo):
    """Gap 4: FR-008 allows it, and the diagram now has the transition."""
    for finding in demo.findings(demo.run()):
        demo.decide(finding["id"])
    url = f"/change-requests/{demo.change_request}"
    assert demo.analyst.get(url).json()["status"] == "REVIEW_COMPLETED"
    assert demo.run()["status"] == "COMPLETED"
    assert demo.analyst.get(url).json()["status"] == "REVIEW_COMPLETED"


def test_restart_marks_interrupted_runs_failed(demo, monkeypatch):
    monkeypatch.setattr("app.modules.analysis.router.run_analysis", lambda _run_id: None)
    run_id = demo.analyst.post(
        f"/change-requests/{demo.change_request}/analysis-runs", json={}
    ).json()["id"]
    pipeline.fail_interrupted_runs()
    run = demo.analyst.get(f"/analysis-runs/{run_id}").json()
    assert run["status"] == "FAILED"
    assert "Interrompue" in run["failure_reason"]
    assert demo.analyst.get(f"/change-requests/{demo.change_request}").json()["status"] == "DRAFT"
