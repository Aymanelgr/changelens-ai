"""Loads the demo into a running platform through its public API, like a real client would.

    python scripts/seed_demo.py            # users, project, documents, code, requirements
    python scripts/seed_demo.py --analyze  # also runs the analysis and prints the findings

Standard library only, so it runs with any Python 3.10+ and no install.
"""

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo-data"
ROLES = {"analyste": "ANALYST", "relecteur": "REVIEWER", "lecteur": "VIEWER"}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = None

    def _send(self, method: str, path: str, data: bytes | None, content_type: str | None):
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def get(self, path: str):
        return self._ok(*self._send("GET", path, None, None))

    def post(self, path: str, body: dict | None = None, allow: tuple[int, ...] = ()):
        status, payload = self._send(
            "POST", path, json.dumps(body or {}).encode(), "application/json"
        )
        return payload if status in allow else self._ok(status, payload)

    def upload(self, path: str, filename: str, content: bytes):
        boundary = uuid.uuid4().hex
        data = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self._ok(
            *self._send("POST", path, data, f"multipart/form-data; boundary={boundary}")
        )

    @staticmethod
    def _ok(status: int, payload):
        if status >= 400:
            raise SystemExit(f"API error {status}: {payload}")
        return payload


def zip_folder(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*.py")):
            archive.write(path, path.relative_to(folder).as_posix())
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()

    env = read_env(args.env_file)
    admin_email = env["BOOTSTRAP_ADMIN_EMAIL"]
    password = env["BOOTSTRAP_ADMIN_PASSWORD"]
    client = Client(args.url)
    client.token = client.post("/auth/login", {"email": admin_email, "password": password})[
        "access_token"
    ]

    projects = client.get("/projects")
    project = next((p for p in projects if p["name"] == "Banque Démo"), None)
    if project is None:
        project = client.post(
            "/projects", {"name": "Banque Démo", "description": "Banque fictive, données synthétiques."}
        )
    base = f"/projects/{project['id']}"

    for name, role in ROLES.items():
        email = f"{name}@demo.local"
        client.post("/users", {"email": email, "password": password}, allow=(409,))
        client.post(f"{base}/members", {"email": email, "role": role})

    for path in sorted((DEMO / "docs").iterdir()):
        client.upload(f"{base}/documents", path.name, path.read_bytes())
    snapshot = client.upload(
        f"{base}/repository-snapshots", "bank-app.zip", zip_folder(DEMO / "bank-app")
    )
    ids = [
        client.upload(
            f"{base}/requirement-sets", name, (DEMO / "requirements" / name).read_bytes()
        )["id"]
        for name in ("requirements_v1.yaml", "requirements_v2.yaml")
    ]

    changes = client.get(f"{base}/change-requests")
    change = changes[-1] if changes else client.post(
        f"{base}/change-requests",
        {"title": "Baisse du seuil des virements", "set_before_id": ids[0], "set_after_id": ids[1]},
    )

    print(f"Project {project['id']} ready, change request {change['id']}.")
    print(f"Code: {snapshot['python_file_count']} Python files, "
          f"{snapshot['unresolved_call_count']} unresolved calls.")
    print(f"Log in as {admin_email} or analyste@ / relecteur@ / lecteur@demo.local")
    print(f"Password: BOOTSTRAP_ADMIN_PASSWORD in {args.env_file}.")

    if not args.analyze:
        return

    run = client.post(f"/change-requests/{change['id']}/analysis-runs")
    while run["status"] in {"QUEUED", "RUNNING"}:
        time.sleep(0.5)
        run = client.get(f"/analysis-runs/{run['id']}")
    print(f"\nRun {run['id']}: {run['status']} in {run['duration_ms']} ms")
    print(json.dumps(run["summary"], indent=2, ensure_ascii=False))
    for finding in client.get(f"/analysis-runs/{run['id']}/findings"):
        print(
            f"\n[{finding['req_key']}] {finding['impact_type']} / {finding['link_type']} "
            f"/ {finding['support']}\n  {finding['target_key']}"
        )
        for evidence in finding["evidence"]:
            print(f"    {evidence['role']}: {evidence['quote'][:90]}")
    print("\nSuggested tests (never executed):")
    for test in client.get(f"/analysis-runs/{run['id']}/test-suggestions"):
        print(f"  {test['input_value']:>12} -> {test['expected']}")


if __name__ == "__main__":
    main()
