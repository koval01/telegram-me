"""Translated documentation builds stay in step with the English one."""

import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LANGUAGES = ("es", "ru")

# Settings a translation sets for itself; everything else must match zensical.toml.
LOCAL_SETTINGS = {
    "site_description",
    "site_url",
    "docs_dir",
    "site_dir",
    "edit_uri",
    "copyright",
    "exclude_docs",
}


def load(name: str) -> dict[str, Any]:
    with (ROOT / name).open("rb") as file:
        project: dict[str, Any] = tomllib.load(file)["project"]
    return project


def nav_pages(nav: list[Any]) -> list[str]:
    """Page paths of a nav in order, without the (translated) section titles."""
    pages: list[str] = []
    for item in nav:
        if isinstance(item, str):
            pages.append(item)
            continue
        for value in item.values():
            pages.extend(nav_pages(value) if isinstance(value, list) else [value])
    return pages


def without_local(config: dict[str, Any]) -> dict[str, Any]:
    shared = {key: value for key, value in config.items() if key not in LOCAL_SETTINGS}
    shared["nav"] = nav_pages(shared["nav"])
    theme = dict(shared["theme"])
    theme.pop("language")
    theme["palette"] = [
        {key: value for key, value in palette.items() if key != "toggle"}
        | {"toggle": {"icon": palette["toggle"]["icon"]}}
        for palette in theme["palette"]
    ]
    shared["theme"] = theme
    shared["extra"] = {key: value for key, value in shared["extra"].items() if key != "alternate"}
    return shared


def markdown_pages(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.md")
        if path.relative_to(root).parts[0] not in LANGUAGES
    }


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_config_matches_english(lang: str) -> None:
    english = load("zensical.toml")
    translated = load(f"zensical.{lang}.toml")
    assert translated["docs_dir"] == f"docs/{lang}"
    assert translated["site_dir"] == f"site/{lang}"
    assert translated["theme"]["language"] == lang
    assert without_local(translated) == without_local(english)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_has_every_page(lang: str) -> None:
    assert markdown_pages(DOCS / lang) == markdown_pages(DOCS)


def test_english_build_excludes_translations() -> None:
    excluded = load("zensical.toml")["exclude_docs"].split()
    assert sorted(excluded) == sorted(f"/{lang}/" for lang in LANGUAGES)


@pytest.mark.parametrize("lang", ("en", *LANGUAGES))
def test_language_switcher_lists_every_language(lang: str) -> None:
    name = "zensical.toml" if lang == "en" else f"zensical.{lang}.toml"
    alternate = load(name)["extra"]["alternate"]
    assert [entry["lang"] for entry in alternate] == ["en", *LANGUAGES]
    current = next(entry for entry in alternate if entry["lang"] == lang)
    assert current["link"] == "./"
