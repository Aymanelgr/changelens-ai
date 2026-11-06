# Continuing development

Work in the same checkout so Git preserves the project's history. Keep changes focused and describe what a reviewer should expect to behave differently.

## Start a change

From the repository root, check for local changes before pulling:

```powershell
git status
git switch main
git pull --ff-only
git switch -c feature/describe-your-change
```

Finish or commit existing work before switching branches. Use a short branch name such as `feature/report-dashboard` or `fix/pdf-sections`.

## Validate

Follow the [README validation instructions](README.md#development-and-validation). Run backend tests for backend behavior changes and the frontend build for frontend changes. Documentation-only changes need checked links, accurate commands, and a review of GitHub's rendered Markdown.

Use a disposable test database. Tests rebuild its schema; never point `TEST_DATABASE_URL` at application data. Model-dependent behavior needs a separate live-model check because automated tests use test doubles.

Create an Alembic migration for schema changes. Update setup instructions when configuration or commands change, and record material capability changes in the [roadmap](docs/ROADMAP.md).

## Commit and push

Review the diff and stage only the intended files:

```powershell
git diff
git status --short
git add README.md
git diff --cached
git commit -m "docs: clarify setup instructions"
git push -u origin HEAD
```

Replace the example file and message with your actual change. Open a pull request into `main` and inspect CI before merging. Future pushes from that branch use `git push`.

For a small personal update made directly on `main`, review, validate, commit, and run `git push origin main`.

## Files to keep local

`.env`, credentials, local uploads, runtime storage, model caches, virtual environments, and frontend dependencies belong outside version control. Use `.env.example` for configuration names and placeholders, and synthetic fixtures for examples.
