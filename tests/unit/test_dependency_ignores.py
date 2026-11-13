"""Ignored advisories stay in step across cargo-deny and osv-scanner (Scorecard)."""

import tomllib
from pathlib import Path

CRATE = Path(__file__).resolve().parents[2] / "rust" / "telegram-me-native"
# osv-scanner reads the file next to each lock file only.
OSV_CONFIGS = [CRATE / "osv-scanner.toml", CRATE / "fuzz" / "osv-scanner.toml"]


def osv_entries(path: Path) -> list[dict[str, object]]:
    # No file means nothing is ignored.
    if not path.exists():
        return []
    entries: list[dict[str, object]] = tomllib.loads(path.read_text("utf-8")).get(
        "IgnoredVulns", []
    )
    return entries


def osv_ids(path: Path) -> set[str]:
    return {str(entry["id"]) for entry in osv_entries(path)}


def test_every_lock_file_has_the_same_osv_ignores() -> None:
    first, *rest = (osv_entries(path) for path in OSV_CONFIGS)
    assert all(entries == first for entries in rest)


def test_osv_ignores_cover_what_cargo_deny_ignores() -> None:
    deny = tomllib.loads((CRATE / "deny.toml").read_text("utf-8"))
    deny_ids = {entry["id"] for entry in deny["advisories"]["ignore"]}
    assert deny_ids <= osv_ids(OSV_CONFIGS[0])


def test_every_ignore_says_why_and_until_when() -> None:
    entries = osv_entries(OSV_CONFIGS[0])
    assert all(entry.get("reason") and entry.get("ignoreUntil") for entry in entries)
