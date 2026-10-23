# Contributing

Thank you for helping to improve TelegramMe! The full guide lives in the
documentation: <https://koval01.github.io/telegram-me/contributing/development/>.

## In short

```bash
git clone https://github.com/<you>/telegram-me && cd telegram-me
make install          # uv sync + pre-commit hooks
git checkout -b feature/my-change
# ... change code and tests ...
make check            # ruff, mypy --strict, tests (offline, ~2 s)
```

- If you changed an API response on purpose, run `make snapshots` and review
  `git diff tests/snapshots`.
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for
  user-visible changes.
- Keep the `/v1` contract backward compatible.
- Open a pull request against `main`; CI also smoke-tests the Docker image
  against live t.me.

Bugs and ideas: [open an issue](https://github.com/koval01/telegram-me/issues).
Security problems: see [SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the
project's [GPL-3.0 License](LICENSE).
