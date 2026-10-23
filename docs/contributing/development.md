# Development

Set up the project as described in
[Local development](../getting-started/local-development.md), then:

## Workflow

1. Create a branch from `main`.
2. Make the change, with tests.
3. `make check` — lint, types and tests, exactly what CI runs first.
4. If you changed what an endpoint returns on purpose, run `make snapshots`
   and review `git diff tests/snapshots` (see [Testing](testing.md)).
5. Add an entry under **Unreleased** in `CHANGELOG.md` for user-visible
   changes.
6. Open a pull request. CI runs lint, mypy, the tests on Python 3.13 and 3.14,
   builds the image and smoke-tests it against live t.me.

## Code style

- Formatting and linting: **ruff** (line length 100, a broad rule set
  including bugbear, pyupgrade, bandit, pydocstyle with Google convention).
  `make format` fixes most findings.
- Types: **mypy --strict** over `src` and `tests`, with the Pydantic plugin.
  Public functions are fully annotated; `Any` is reserved for raw JSON.
- Docstrings: every public module, class and function, Google style.
- Layering: `api` → `services` → `telegram` / `cache`; nothing below `api`
  imports FastAPI. Wire new long-lived objects in `container.py`.
- Errors: raise the domain errors from `telegram/errors.py`; the API maps them
  to status codes in one place (`api/errors.py`).
- Keep the `/v1` contract stable. New fields are fine; removing or renaming
  fields, or changing types, needs a new API version.

## Dependencies

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`:

```bash
uv add <package>             # runtime dependency
uv add --dev <package>       # development tool
uv lock --upgrade            # refresh everything to the newest versions
```

Dependabot opens grouped weekly updates for Python packages, GitHub Actions
and Docker base images.

## Pre-commit

`make install` installs git hooks that run ruff (with autofix), ruff format,
mypy and a few hygiene checks on every commit, using the versions from
`uv.lock`. Run them on demand with `uv run pre-commit run --all-files`.
