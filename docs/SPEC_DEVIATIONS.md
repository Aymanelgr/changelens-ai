# Differences from the specification

Each one is a deliberate decision. Be ready to defend them in front of the jury.

## Gaps found in the specification and fixed

| # | Problem | Decision | Where |
|---|---|---|---|
| 1 | `Finding` had one `run_id`, yet re-analysis must update the same finding and a finished run must never change. | New table `finding_occurrence`: what one run saw. `Finding` keeps the lasting identity and current status. `Evidence` belongs to the occurrence. | `analysis/models.py` |
| 2 | `TestSuggestion` required a `finding_id`, but step 9 builds tests from the `ChangeFact`, independently of findings. | Tests belong to the run and the requirement. `finding_id` is optional. They have their own endpoint, `GET /analysis-runs/{id}/test-suggestions`. | `analysis/models.py` |
| 3 | A failed analysis always returned the request to `DRAFT`, even with reviewed findings. | The run stores `cr_status_before` and restores it. | `analysis/pipeline.py` |
| 4 | FR-008 allows an analysis from `REVIEW_COMPLETED`, but the diagram had no such transition. | Transition added. After a run: `IN_REVIEW` if anything is still `PROPOSED`, otherwise `REVIEW_COMPLETED`. | `analysis/router.py` |
| 5 | Found during the build: the fingerprint did not include the change request. A later change to the same requirement would inherit old decisions. | `change_request_id` is part of the fingerprint. | `analysis/pipeline.py` |
| 6 | Found during the build: the diagram had no exit from `SUPERSEDED`, although a finding can come back. | If an analysis produces it again, it returns to `PROPOSED`, and this is audited. | `analysis/pipeline.py` |

## Other decisions

| Topic | Specification | Implementation | Why |
|---|---|---|---|
| Archive extraction | Extract into an isolated folder | Read in memory, never written to disk | No file from the archive reaches the disk, so path attacks have nothing to act on. Dangerous paths are still refused. |
| File time limit | Maximum time per file | Maximum size per file (1 MB) | `ast.parse` cannot be interrupted from a thread. Size bounds the same risk. |
| Module `projects` | Not in the module list | Added | Projects and members are a domain of their own. Spreading them over `auth` would blur both. |
| Requirements without an ID | Similarity-based fallback | Refused with the line number | FR-004 refuses an empty identifier, which conflicts with the fallback. FR-004 won. |
| Operator check | Code operator ≠ requirement operator | The logical complement counts as consistent | `if amount <= 10000: skip` states the same rule as `> 10000 triggers`. Flagging it would be a false alarm. |
| `STRONG` support | Only with EXPLICIT or STATIC evidence | Those, plus: the value is used in a comparison, or carries the right unit | The specification states a necessary condition. Without the extra rule, `CACHE_SIZE = 10000` would be STRONG. |
| Abstentions | Not specified | They need a decision but create no trace link, and quote the requirement text | An abstention links the requirement to nothing. Quoting the text keeps OBJ-4 (every finding has a citation) true. |
| Account creation | `POST /auth/login` only | First admin from environment variables, then `POST /users` by a global admin | The specification did not say how the first user comes to exist. |
| Audit protection | No route allows modification | Same, plus a database trigger refusing `UPDATE` and `DELETE` | The guarantee holds even against a bug in the application. |

## Conflict to resolve later

FR-018 keeps a minimal audit event and deletes the rest when a project is deleted, but the trigger refuses every `DELETE` on `audit_event`. When implementing FR-018, decide between a controlled purge path and keeping all audit events.
