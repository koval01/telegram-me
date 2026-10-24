#!/usr/bin/env python3
"""Check that the coverage floors only go up.

The floors CI enforces live next to the code they measure:

- Python: `fail_under` in `[tool.coverage.report]` of pyproject.toml (pytest-cov);
- Rust: `fail-under-lines` in `[package.metadata.coverage]` of
  rust/telegram-me-native/Cargo.toml (cargo llvm-cov, line coverage).

    python3 scripts/coverage_floor.py --base origin/main

Fails when a floor is lower than at `--base`: raise a floor together with the
tests that earn it, never lower one to let a change through.
Standard library only.
"""

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

FLOORS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Python": ("pyproject.toml", ("tool", "coverage", "report", "fail_under")),
    "Rust": (
        "rust/telegram-me-native/Cargo.toml",
        ("package", "metadata", "coverage", "fail-under-lines"),
    ),
}


def _floor(text: str | None, keys: tuple[str, ...]) -> float | None:
    if text is None:
        return None
    value: Any = tomllib.loads(text)
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return float(value)


def _at(ref: str, path: str) -> str | None:
    result = subprocess.run(  # noqa: S603 - fixed command line
        ["git", "show", f"{ref}:{path}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    """Compare each floor with the one at --base."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="Commit to compare with, e.g. origin/main")
    args = parser.parse_args()
    lowered = False
    for name, (path, keys) in FLOORS.items():
        now = _floor(Path(path).read_text(encoding="utf-8"), keys)
        before = _floor(_at(args.base, path), keys)
        if now is None:
            print(f"::error::{name} coverage floor missing ({path}: {'.'.join(keys)})")
            lowered = True
        elif before is not None and now < before:
            print(f"::error::{name} coverage floor lowered from {before:g}% to {now:g}% ({path})")
            lowered = True
        else:
            was = "none" if before is None else f"{before:g}%"
            print(f"{name} coverage floor: {now:g}% (was {was})")
    return 1 if lowered else 0


if __name__ == "__main__":
    sys.exit(main())
