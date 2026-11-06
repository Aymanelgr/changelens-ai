"""Shared test setup. Integration tests run against a real PostgreSQL database, because
triggers, partial indexes and row locks are part of the behaviour being tested."""

import io
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA = REPO_ROOT / "demo-data"


def _test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    values = {}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    user, password = values.get("POSTGRES_USER"), values.get("POSTGRES_PASSWORD")
    return f"postgresql+psycopg://{user}:{password}@127.0.0.1:5433/changelens_test"


_url = _test_database_url()
# These tests empty every table, so a wrong URL must never reach a real database.
if "test" not in urlsplit(_url).path:
    raise RuntimeError("Refusing to run: the test database name must contain 'test'.")

# Must be set before anything from `app` is imported: settings are read at import time.
os.environ["DATABASE_URL"] = _url
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="changelens-test-")
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""
# Tests never download a model or reach a real LLM. Those that need them inject fakes.
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBEDDING_PROVIDER"] = "none"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.models import User  # noqa: E402

PASSWORD = "demo-password-123"
_PASSWORD_HASH = hash_password(PASSWORD)  # hashing is slow on purpose: do it once


@pytest.fixture(scope="session")
def migrated_database():
    """Builds the schema through the real migrations, which tests the migrations too."""
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")


@pytest.fixture
def clean_tables(migrated_database):
    """DELETE rather than TRUNCATE: TRUNCATE forces a disk sync per table, which took
    seconds per test on Docker for Windows. The replica role skips the append-only trigger
    and foreign keys for this one transaction. It needs a superuser, as test databases have."""
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        for table in Base.metadata.sorted_tables:
            connection.execute(table.delete())


@pytest.fixture
def client(clean_tables):
    with TestClient(app) as test_client:
        yield test_client


@dataclass
class Actor:
    """A logged-in user: thin helpers around the test client."""

    client: TestClient
    email: str
    headers: dict[str, str]

    def get(self, url, **kwargs):
        return self.client.get(url, headers=self.headers, **kwargs)

    def post(self, url, **kwargs):
        return self.client.post(url, headers=self.headers, **kwargs)

    def upload(self, url, filename, content):
        if isinstance(content, str):
            content = content.encode()
        return self.post(url, files={"file": (filename, content)})


def make_actor(client: TestClient, email: str, *, is_admin: bool = False) -> Actor:
    with SessionLocal() as db:
        db.add(User(email=email, password_hash=_PASSWORD_HASH, is_admin=is_admin))
        db.commit()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return Actor(client, email, {"Authorization": f"Bearer {token}"})


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def demo_app_files(**overrides: str | None) -> dict[str, str]:
    root = DEMO_DATA / "bank-app"
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    }
    for name, content in overrides.items():
        path = name.replace("__", "/") + ".py"
        if content is None:
            files.pop(path)
        else:
            files[path] = content
    return files


@dataclass
class DemoProject:
    id: int
    admin: Actor
    analyst: Actor
    reviewer: Actor
    viewer: Actor
    set_v1: int
    set_v2: int
    change_request: int

    def run(self) -> dict:
        response = self.analyst.post(
            f"/change-requests/{self.change_request}/analysis-runs", json={}
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        run = self.analyst.get(f"/analysis-runs/{run_id}").json()
        assert run["status"] == "COMPLETED", run
        return run

    def findings(self, run: dict, **params) -> list[dict]:
        response = self.analyst.get(f"/analysis-runs/{run['id']}/findings", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def decide(self, finding_id: int, decision: str = "ACCEPT", **extra):
        return self.reviewer.post(
            f"/findings/{finding_id}/decisions", json={"decision": decision, **extra}
        )


def find_one(findings, *, target_contains: str, impact: str | None = None) -> dict:
    matches = [
        f
        for f in findings
        if target_contains in f["target_key"] and (impact is None or f["impact_type"] == impact)
    ]
    assert len(matches) == 1, (target_contains, impact, [f["target_key"] for f in findings])
    return matches[0]


def _upload_ok(actor: Actor, url: str, filename: str, content) -> dict:
    response = actor.upload(url, filename, content)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def demo(client) -> DemoProject:
    """The specification's running example, imported through the real API."""
    admin = make_actor(client, "admin@test.local")
    analyst = make_actor(client, "analyst@test.local")
    reviewer = make_actor(client, "reviewer@test.local")
    viewer = make_actor(client, "viewer@test.local")

    project_id = admin.post("/projects", json={"name": "Banque Démo"}).json()["id"]
    for actor, role in ((analyst, "ANALYST"), (reviewer, "REVIEWER"), (viewer, "VIEWER")):
        response = admin.post(
            f"/projects/{project_id}/members", json={"email": actor.email, "role": role}
        )
        assert response.status_code == 200, response.text

    base = f"/projects/{project_id}"
    for path in sorted((DEMO_DATA / "docs").iterdir()):
        _upload_ok(analyst, f"{base}/documents", path.name, path.read_bytes())
    _upload_ok(analyst, f"{base}/repository-snapshots", "bank-app.zip", zip_bytes(demo_app_files()))
    requirements = DEMO_DATA / "requirements"
    set_v1 = _upload_ok(
        analyst,
        f"{base}/requirement-sets",
        "requirements_v1.yaml",
        (requirements / "requirements_v1.yaml").read_bytes(),
    )["id"]
    set_v2 = _upload_ok(
        analyst,
        f"{base}/requirement-sets",
        "requirements_v2.yaml",
        (requirements / "requirements_v2.yaml").read_bytes(),
    )["id"]
    change_request = analyst.post(
        f"{base}/change-requests",
        json={"title": "Baisse du seuil", "set_before_id": set_v1, "set_after_id": set_v2},
    ).json()["id"]
    return DemoProject(project_id, admin, analyst, reviewer, viewer, set_v1, set_v2, change_request)
