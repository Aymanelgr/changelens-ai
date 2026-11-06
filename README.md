# ChangeLens AI

When a requirement changes, which documents, code and tests must be reviewed, and on what evidence?

ChangeLens AI compares two versions of a requirement, proposes what is probably impacted while citing the exact file and line or document and section, suggests boundary tests, and lets a human accept, reject or correct each proposal. Everything is kept in an append-only audit trail.

This is a final-year project (PFE) built on synthetic data, for demonstration and not for production.

**Learning this project?** Start with [docs/LEARNING_JOURNAL.md](docs/LEARNING_JOURNAL.md), which explains every step and why it was done that way.

## Status

| Milestone (specification, 6.13) | State |
|---|---|
| J1: diff and ChangeFacts visible | Done |
| J2: full flow without LLM (change → findings → decision) | Done |
| J3: full flow with LLM, search and PDF (weeks 5 to 8) | Not started |
| J4: report, impact map (weeks 9 and 10) | Not started. Audit and boundary tests are already done |
| J5: AC-07 to AC-10 (weeks 11 and 12) | Tested without LLM. Project deletion (FR-018) is not done |

Not there yet: PDF import (refused with a clear message), embeddings and hybrid search, the LLM, report export, the impact map, the evaluation set. The analysis only finds what deterministic rules can find.

## Run it

Requires Docker Desktop and Python 3.10+.

```powershell
python scripts/generate_env.py    # creates .env with random secrets, ignored by git
docker compose up -d --build
python scripts/seed_demo.py       # optional: loads the fictional bank through the API
```

Open <http://127.0.0.1:8080>. Log in as `admin@demo.local`, `analyste@demo.local`, `relecteur@demo.local` or `lecteur@demo.local`. The password is `BOOTSTRAP_ADMIN_PASSWORD` in `.env`.

Interactive API documentation: <http://127.0.0.1:8000/docs>.

Demo path: open the project, open the change request, click **Lancer l'analyse**, review the queue, then read the audit history.

## Develop

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt

# Tests use a separate database. Create it once, with the db container running:
docker compose exec db createdb -U changelens changelens_test

.venv\Scripts\python -m pytest --cov=app    # tests
.venv\Scripts\ruff check .                  # linter
.venv\Scripts\ruff format .                 # formatter
```

```powershell
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173, forwards /api to the backend on port 8000
npm run build      # type check and production build
```

## Layout

```
backend/app/core/       configuration, database, security, shared vocabularies
backend/app/modules/    one folder per domain: auth, projects, ingestion, extraction,
                        requirements, code_analysis, analysis, review, audit
backend/migrations/     database schema history (Alembic)
backend/tests/          unit tests (no database) and integration tests (real PostgreSQL)
frontend/src/           React pages and API client
demo-data/              the fictional bank, including deliberate traps
scripts/                .env generation and demo seeding
docs/                   learning journal and differences from the specification
```

## Known limits

- Static analysis is an approximation. Dynamic calls (`getattr`, dependency injection), methods on objects of unknown type and values in configuration files are invisible. Unresolved calls are counted and shown.
- A constant used indirectly (`LIMIT = 10000`, then `if x > LIMIT`) is found at its definition only. The function using it is not linked yet: finding such links is what search and the LLM are for.
- A finding means "probably worth reviewing". It never proves that a requirement is correctly implemented, and a suggested test is never executed.
- Analyses run inside the API process. A restart interrupts them, and they are then marked FAILED.
- The per-file analysis time limit from section 4.8 is replaced by a size limit (1 MB per Python file), because `ast.parse` cannot be interrupted from a thread.

Differences from the specification are listed in [docs/SPEC_DEVIATIONS.md](docs/SPEC_DEVIATIONS.md).
