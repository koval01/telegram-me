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
- Layering: `api` → `services` → `cache` → `telegram`; nothing below `api`
  imports FastAPI. import-linter checks both (`[tool.importlinter]` in
  `pyproject.toml`). Wire new long-lived objects in `container.py`.
- Rust: no `unwrap()` in production code (an `expect("...")` states the
  invariant instead), and in `src/parse/` no indexing or slicing that can
  panic: use `get`, `split_once`, `strip_prefix`. The extension aborts on a
  panic, which takes the worker down. See
  [Static checks](testing.md#static-checks) for every tool.
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
and Docker base images, for releases at least a week old (security updates
come at once).

## Pre-commit

`make install` installs git hooks that run on every commit: ruff (with
autofix), ruff format, mypy, import-linter, vulture, typos, zizmor, gitleaks
on the staged changes, cargo fmt and clippy, and, when their files change,
hadolint, cargo-deny and cargo-machete. The Python tools come from
`uv.lock`; `make tools` installs the others. Run them on demand with
`uv run pre-commit run --all-files`.

## Documentation

The site is written in English in `docs/`, with Spanish and Russian
translations in `docs/es/` and `docs/ru/` that mirror its file layout. Each
language is its own build: `zensical.toml` builds English into `site/`, and
`zensical.es.toml` / `zensical.ru.toml` build the translations into
`site/es/` and `site/ru/`. The language switcher in the header keeps the reader
on the same page and section (`overrides/main.html`).

```bash
make docs-serve                  # live preview of English on :8001
make docs-serve DOCS_LANG=ru     # live preview of the Russian pages
make docs-preview                # build all languages and serve site/ with the switcher
```

When you change a page, update its translations in the same pull request, or
open an issue for them. Translated headings keep the English anchor with an
explicit id (`## Ejemplo { #example }`), so links and the switcher land on the
same section in every language. Code, commands, setting names and JSON keys
are not translated. `tests/unit/test_docs_config.py` checks that every page
has a translation and that the three configuration files stay in sync.
