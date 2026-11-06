# Learning journal

This is the full account of how ChangeLens AI reached milestone J2: what was done at each step, why, what went wrong, and how to practise it yourself.

You will defend this project, so don't just read this. For each step, open the files, then do the exercise without looking. **If you can't explain a piece of code to the jury, rewrite it until you can.**

**Suggested reading order:** the steps below follow the build, but the code reads best in the order of Step 2, then 3, then 4.

---

## The big picture

```
Browser (React) ──HTTP/JSON──► nginx ──► FastAPI ──► PostgreSQL
                                           │
                                           └─ background task: the analysis pipeline
```

What happens when you click **Lancer l'analyse**:

1. `POST /change-requests/1/analysis-runs` creates a run in state `QUEUED`, records the exact input versions, and answers `202 Accepted` at once.
2. A background task compares the two requirement sets (`diffing.py`) and extracts facts (`facts.py`).
3. For each changed requirement, `candidates.py` searches for the old value and the requirement ID, then follows the call graph one hop.
4. Every citation is checked against its source. A citation that isn't literally there is dropped.
5. Findings are saved. If the fingerprint already exists, the existing finding is updated.
6. The browser polls every 1.5 s, sees `COMPLETED`, and shows the review queue.
7. A reviewer decides. Only that decision makes a trace link `CONFIRMED`.

**The idea to remember:** the LLM isn't there yet, and the tool already works. That's on purpose. The specification calls this the baseline: later, you will measure what the LLM adds on top of it.

---

## Step 0: Check the machine before designing anything

**What I did:** checked the tools, versions, RAM, GPU, free disk and open ports.

**Why:** it decides how everything runs. Docker and Ollama were installed, an RTX 3080 and 32 GB of RAM cover a local LLM, and the specification's risk table says to check hardware in week 1.

**What I learned about your machine:** PostgreSQL's usual port was free, but I used **5433** anyway, so the project can't clash with a PostgreSQL you might install later.

**Try it:** run `docker compose ps` and explain each line of its output.

---

## Step 1: Scaffolding

**Files:** `docker-compose.yml`, `backend/Dockerfile`, `.gitignore`, `.env.example`, `scripts/generate_env.py`

### Concepts

**Containers.** Each service (database, backend, frontend) runs isolated, with its own dependencies. `docker compose up` means anyone can start the project with one command (NFR-008).

**Secrets belong outside the code.** `.env` holds the real values and is ignored by git. `.env.example` documents the names with fake values and is committed. Check it yourself: `git check-ignore .env`.

**Fail loudly.** In `docker-compose.yml`, `${JWT_SECRET:?set JWT_SECRET in .env}` refuses to start if the value is missing. In `config.py`, `jwt_secret` has no default and a minimum length. A wrong setup stops immediately, instead of running with a weak secret.

**Details worth noticing in `Dockerfile`:**

- `requirements.txt` is copied *before* the code. Docker caches each layer, so changing your code doesn't reinstall every dependency.
- `USER changelens`: the API handles untrusted uploads, so it doesn't run as root. Verified: `docker compose exec backend id` prints uid 1000.
- Ports are bound to `127.0.0.1`, so your demo isn't visible to others on the same Wi-Fi.

### What went wrong

My first secret generator was a one-line `python -c` in PowerShell. PowerShell 5.1 strips double quotes when calling programs, so it crashed and left an empty `.env`. That turned into a useful accident: Compose refused to start, which proved the `:?` guard works. **Lesson:** past simple one-liners, write a script file.

**Try it:** temporarily rename `.env` and run `docker compose config`. Read the error. Rename it back.

---

## Step 2: The deterministic core

Pure Python: no web, no database. These modules are the heart of the project, and the easiest to test.

### 2.1 `requirements/facts.py`: understanding a requirement

Turns `"supérieurs à 10 000 MAD"` into `{value: 10000, unit: MAD, operator: ">"}`.

**Why not use an LLM here?** A threshold change must be detected the same way every time. A regular expression is exact, free, instant and testable. Use a model only where rules can't do the job.

**Regular expressions to study:**

- `(?<![\w-])`: a *lookbehind*. It refuses a number preceded by a letter, digit or hyphen, so `REQ-012` and `v2` aren't quantities.
- Longest wording first: `supérieur ou égal à` must be tried before `supérieur à`.

**Number parsing is harder than it looks.** `10.000` and `10,000` are ten thousand, while `1,5` is one and a half. The rule in `parse_number`: one separator followed by exactly 3 digits means thousands. Otherwise it marks decimals.

### 2.2 `requirements/diffing.py`: comparing versions

Sets do the work: `old.keys() | new.keys()` gives every requirement key, and each is then ADDED, REMOVED, MODIFIED or UNCHANGED.

**Subtle point:** the word diff keeps `10 000` as one token. Otherwise it would show `10 → 5` with `000` unchanged, which is true for the computer and misleading for a person.

### 2.3 `requirements/boundary_tests.py`: why S-1, S, S+1

Most threshold bugs sit exactly at the threshold.

| Operator | S - 1 | S | S + 1 |
|---|---|---|---|
| `>` | no | **no** | yes |
| `>=` | no | **yes** | yes |

That middle column is the whole point of "supérieur à" versus "supérieur ou égal à".

### 2.4 `extraction/segmenter.py`: citable pieces

The rule that everything relies on: `text[start_char:end_char] == segment.text`. That's what makes a citation verifiable. It's tested, including with Windows line endings.

### 2.5 `code_analysis/archive.py`: a zip is an attack surface

| Attack | Defence |
|---|---|
| **Zip slip**: an entry named `../../evil.py` overwrites your files | Paths refused, and files are never written to disk at all |
| **Zip bomb**: 1 MB expands to 10 GB | Declared size checked first, compression ratio limit, and every read is bounded (declared sizes can lie) |
| **Symlink**: a link to `/etc/passwd` | Refused |
| **Leaked secret** | `.env` and key files excluded, so they never reach an index or an LLM |

### 2.6 `code_analysis/analyzer.py`: reading code without running it

**The most important security property in the project: uploaded code is parsed, never executed.** `ast.parse` turns text into a tree and runs nothing. `NFR-006` is tested with a trapped file that would write to disk if it ever ran.

Compare two tools:

```python
import ast
tree = ast.parse("if amount > 10000:\n    check()")
print(ast.dump(tree, indent=2))
```

- `ast` sees structure, but **comments don't exist in the tree**. So `tokenize` is also used, to find `# REQ-012`.
- `visit_Compare` normalizes direction: `10000 < amount` is recorded as `amount > 10000`.

**Honest limit:** the call graph is an approximation. `getattr(obj, name)()` can't be resolved statically, so it's recorded as unresolved and the count is shown to the user. Showing a limit is better than hiding it, and a jury will respect that.

**Try it:**

1. Before running it, predict what this returns. Then check:
   `compute_change_facts("au plus 3 tentatives", "au moins 3 tentatives")`
2. Add a new operator wording to `_OPERATORS`, such as `n'excédant pas`. Write the test first.
3. Add `import os; os.system("calc")` to a demo file and re-upload it. Nothing opens. Why not, exactly?

---

## Step 3: Database design

**Files:** the `models.py` of each module, `migrations/versions/0001_initial_schema.py`

### The four design fixes, which are your best material for the defense

Reading a specification critically is a skill. These gaps were in the document, and finding them is real engineering work. Full table: [SPEC_DEVIATIONS.md](SPEC_DEVIATIONS.md).

**Gap 1: `Finding` versus "a finished run never changes".** The specification gave each finding one `run_id`. Yet a re-analysis must update the same finding, and a finished run must stay untouched. Those two rules can't both hold with one table.

The fix separates two ideas: **identity** (`Finding`, the lasting thing a human decides on) and **observation** (`FindingOccurrence`, what one run saw). Test: `test_finished_run_keeps_what_it_saw`.

**The fingerprint** answers "is this the same finding as last time?" It hashes the project, change request, requirement, target and impact type. It uses `transfers/validation.py::validate_transfer` rather than a line number, because a line number changes whenever someone adds an import above it.

I added the change request to the hash myself, because otherwise REQ-012 changing a *second* time would silently inherit decisions made about the first change. Be ready to explain that one.

### Let the database enforce the rules

Application code has bugs, and two requests can run at the same moment.

| Rule | Mechanism | Why not just check in Python? |
|---|---|---|
| One active run per change request | Partial unique index on `status IN ('QUEUED','RUNNING')` | Two simultaneous clicks would both pass a Python check |
| Audit is append-only | Trigger refusing `UPDATE` and `DELETE` | Holds even against a bug or a manual SQL session |
| Two reviewers deciding at once | `SELECT ... FOR UPDATE` row lock | The second waits, then gets a 409 |
| Test status can only be `SUGGESTED` | `CHECK` constraint with one value | AI-007 becomes impossible to violate |

### Migrations

A migration is a versioned, repeatable change to the schema. Alembic compared the models with the empty database and generated most of it. **Autogenerate can't see triggers**, so I added the audit trigger by hand. Always read what it generates.

**Try it:** add a nullable `priority` column to `ChangeRequest`, then:

```powershell
.venv\Scripts\alembic revision --autogenerate -m "add priority"
```

Read the generated file, then delete it. (Note: `alembic` needs `DATABASE_URL` set in your shell, pointing at port 5433.)

---

## Step 4: The API and the pipeline

### Structure: a modular monolith

One deployable app with clear internal modules. Each has `models.py`, its pure logic, and a `router.py`. You get clean boundaries without the operational cost of microservices, which a solo student can't justify.

### Authentication and authorization are different questions

- **Authentication (`get_current_user`):** who are you? JWT signed with a secret, passwords hashed with Argon2.
- **Authorization (`require_role`):** what may you do in this project? Checked on the server, on every endpoint.

**A detail to know:** a non-member gets **404**, not 403. A 403 would confirm the project exists. A member with too low a role gets 403, because they already know it exists.

Login failures give the same message and take similar time for known and unknown emails (`_DUMMY_HASH`), so attackers can't discover which emails exist.

### Why `POST` returns `202 Accepted`

An analysis takes time. The server answers immediately with a run ID, and the browser polls for the result. The specification explicitly accepts the limit: a restart kills running analyses. `fail_interrupted_runs` handles that honestly at startup, and test `test_restart_marks_interrupted_runs_failed` proves it.

**A classic trap:** the background task opens its own database session (`run_analysis`), because the request's session is closed by the time the task runs.

### Evidence is verified before anyone sees it

In `pipeline.py`, every citation must be a literal substring of its source (NFR-004). This check will matter most once the LLM arrives: a model can invent a quote, but it can't get an invented quote past this check.

### Link type comes from the source, never from a model

`EXPLICIT`, `STATIC` and `INFERRED` are decided by *where the evidence came from* (AI-003). `support` is a rule-based category, not a percentage (AI-004). A model's self-reported confidence isn't a calibrated probability, so it's never displayed as one.

**Try it:** with the stack running, open <http://127.0.0.1:8000/docs>, click **Authorize**, and call an endpoint by hand. Then try reading a project with a user who isn't a member.

---

## Step 5: Testing, where the most interesting things happened

**161 tests. 97% coverage on the critical modules, required: 70% (NFR-009).** These measure code executed by tests, not that the product is right: coverage can't see a misleading screen, as you'll see below.

### The test pyramid here

- **Unit tests** (`tests/unit/`): pure logic, run in seconds.
- **Integration tests** (`tests/integration/`): through the real API with a real PostgreSQL. They need a real database, because triggers, partial indexes and row locks *are* the behaviour being tested.
- **Acceptance tests** (`test_acceptance.py`): the specification's own scenarios, named `test_ac01_...` to `test_ac10_...`. You can show a jury a direct line from requirement to proof.

### Every failure I hit, and what it teaches

| What happened | Real cause | Lesson |
|---|---|---|
| Unit tests hung for 3 minutes | `TRUNCATE` forces a disk sync per table, very slow on Docker for Windows, and it ran before every test, even without a database | Diagnose before guessing. `pg_stat_activity` showed `DataFileImmediateSync` directly |
| `supérieurs ou égaux à` not recognized | My regex knew `égal`, `égale`, `égales`, but not the irregular plural `égaux` | Test with realistic language. French has irregular plurals |
| `could not identify an equality operator for type json` | `SELECT DISTINCT` compares whole rows, and PostgreSQL's `json` type has no equality | Select only what you need: here, the IDs |
| `_IncludedRouter has no attribute path` | A newer FastAPI changed `app.routes` internals | Depend on public interfaces. The OpenAPI schema is one |
| A `# REQ-012` comment produced an empty quote | The comment sat above the `def`, outside the function's stored text, and the citation check caught it | **The safety net worked on its own author's bug** |
| `CACHE_SIZE` was reported STRONG | My demo comment said "Sans rapport avec REQ-012", so the tool correctly saw an explicit citation | Sometimes the tool is right and your test data is wrong |

### What only a real browser caught

All 160 tests passed, and the first screenshot still showed two real problems:

1. **`>=` was displayed as `≥`.** A font ligature. In a tool whose whole point is telling `>` from `>=`, that's misleading. No API test can see it.
2. **The explanation said "ancienne version" but the citation showed the new text.** Each was correct alone, and together they made no sense. The fix now has its own test.

My first ligature fix also failed, because the CSS `font:` shorthand resets ligature settings. I only knew because I measured the computed style instead of assuming.

**Lesson: passing tests prove what you thought to test. Look at the real thing.**

### Make sure tests can fail

A test with `try: ... except: pass` can't fail, so it proves nothing. I had written one and replaced it. Before trusting a test, break the code on purpose and watch it fail.

**Try it, this is the most valuable exercise here.** Break each of these, predict which test fails, run `pytest`, then undo:

1. In `deps.py`, make `require_role` always return without checking.
2. In `pipeline.py`, remove `change_request_id` from `fingerprint`.
3. In `boundary_tests.py`, change `">": lambda x, s: x > s` to `x >= s`.
4. In `candidates.py`, make `is_verified` always return `True`. Does a test catch this? If not, write one.

---

## Step 6: The frontend

**Files:** `frontend/src/`

- **`api.ts`:** one place for all HTTP calls, the token and error messages.
- **`ChangeRequestPage.tsx`:** the main screen. Note the polling `useEffect`, which runs only while an analysis runs and cleans up its timer.
- **TypeScript in strict mode:** `types.ts` describes the API's shapes, so many mistakes fail at build time.

**Design choices tied to requirements:**

- NFR-010: evidence and decision buttons share one screen. A decision takes 2 clicks: select, accept.
- Limits are displayed, not hidden: "5 appels non résolus : du code dépendant peut manquer".
- Tests are labelled "aucun de ces tests n'a été exécuté" (AI-007).
- The button appears only if your role allows it. **That's convenience, not security**: the server still checks every call.

**Try it:** add a "next finding" behaviour that selects the next `PROPOSED` finding after a decision. It's a real improvement I left for you, and it touches state, props and effects.

---

## Step 7: End-to-end verification

1. Built all three containers from scratch.
2. Loaded the demo through nginx with `scripts/seed_demo.py`, as an external client would.
3. Walked the interface in a real browser, including a wrong password, the reject flow and phone width.

**One thing to know:** I tested the UI on a throwaway instance with its own database and a test-only password, then deleted it. The permission system had blocked me from printing your real password into the session, which was the right call, and the throwaway approach was better anyway.

---

## What is not done, and not verified

- **CI has never run.** `.github/workflows/ci.yml` needs a GitHub repository. Each command in it passed locally, but the workflow file itself is unproven, and that includes the gitleaks step.
- **NFR-008 not tested:** a classmate installing it in under 30 minutes using only the README. That's a real test only they can do.
- **Performance targets not measured** (NFR-001 to 003). An analysis took about 80 ms on the small demo, which says nothing about 200 files with an LLM.
- **Nothing is committed.** The repo is initialized with no commits, so your first commit is yours to make.

---

## Your next steps

**Before writing new code**, make sure you can do these from memory:

1. Draw the data model on paper and explain `Finding` versus `FindingOccurrence`.
2. Explain why the fingerprint avoids line numbers.
3. Do the "break it on purpose" exercise in Step 5.

**Next milestones**, in the specification's order:

1. **Weeks 5 and 6:** PDF extraction, embeddings, hybrid search, and the start of the evaluation set. Create the held-out set **early**, and never tune on it (AI-010).
2. **Weeks 7 and 8:** the LLM. `LLMClient` interface, Ollama, versioned prompt, schema validation. The citation check is already there.

The specification's part D (evaluation format and metrics) is still unwritten, and it decides what `evaluation/` contains. Settle it before week 5.
