"""Creates a .env with random secrets. Refuses to overwrite one that already has content."""

import secrets
from pathlib import Path

env = Path(__file__).resolve().parent.parent / ".env"
if env.exists() and env.read_text().strip():
    raise SystemExit(f"{env} already exists: nothing changed.")

env.write_text(
    "POSTGRES_USER=changelens\n"
    f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}\n"
    "POSTGRES_DB=changelens\n"
    f"JWT_SECRET={secrets.token_urlsafe(48)}\n"
    "BOOTSTRAP_ADMIN_EMAIL=admin@demo.local\n"
    f"BOOTSTRAP_ADMIN_PASSWORD={secrets.token_urlsafe(12)}\n"
)
print(f"Created {env}")
