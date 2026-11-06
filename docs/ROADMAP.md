# ChangeLens AI roadmap

This roadmap describes the current source. An implemented feature is not a claim of production readiness or live-model accuracy.

## Implemented

| Area | Available in source |
|---|---|
| Requirement changes | Versioned YAML imports, word differences, structured change facts |
| Inputs | Markdown, text, text-based PDF extraction, Python repository snapshots |
| Analysis | Static rules, keyword/vector retrieval, local LLM analysis, checked citations, fallback modes |
| Review | Accept/reject/correct decisions, trace links, evidence changes, boundary-test suggestions |
| Reporting APIs | Markdown/HTML reports and a requirement-to-impact graph |
| Governance | Project roles, protected audit events, controlled project deletion |
| Development checks | Backend tests, lint/format checks, frontend build, secret scanning, Docker builds in CI |

## Next steps

- [ ] Connect reports and the impact map to the dashboard.
- [ ] Build a versioned evaluation dataset with separate development and held-out examples.
- [ ] Record retrieval, citation, and impact-analysis results with model and prompt versions.
- [ ] Validate local-model runs end to end, including missing models, slow responses, and resource limits.
- [ ] Improve links for indirect constant use and document remaining static-analysis gaps.
- [ ] Refresh historical learning and specification notes against the current implementation.

## Outside the current implementation

Scanned-document OCR, automatic source-code edits, automatic test execution, and a production deployment are not provided. Analysis currently runs inside the API process rather than in a durable job queue.

See the [README](../README.md) for setup and the [contribution guide](../CONTRIBUTING.md) for the update workflow.
