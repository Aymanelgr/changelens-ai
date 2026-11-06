# Demo data: a fictional bank

Everything here is synthetic (DATA-003) and written for this project (DATA-006).

| Path | Purpose |
|---|---|
| `requirements/requirements_v1.yaml`, `requirements_v2.yaml` | The two requirement versions |
| `docs/` | Documentation with the old threshold, written three different ways |
| `docs-corrected/guide_utilisateur.md` | Same file name, new threshold, for the re-analysis scenario (AC-08) |
| `bank-app/` | The Python code to analyze. It is never run. |

Traps planted on purpose:

- `transfers/limits.py` has `RECENT_TRANSFERS_CACHE_SIZE = 10000`, and the guide says "10 000 clients". Same number, no relation to REQ-012. The tool proposes them at MODERATE support, and a human rejects them.
- `reporting/alerts.py` uses `>=` where the requirement says "supérieur à" (AC-05).
- `reporting/dispatch.py` uses `getattr`, which static analysis cannot follow. It shows up as an unresolved call.
- REQ-031 is deliberately vague (AC-06).
