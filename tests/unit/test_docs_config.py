"""Translated documentation builds stay in step with the English one."""

import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from telegram_me.settings import Settings

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


@pytest.mark.parametrize("lang", ["en", *LANGUAGES])
def test_language_switcher_lists_every_language(lang: str) -> None:
    name = "zensical.toml" if lang == "en" else f"zensical.{lang}.toml"
    alternate = load(name)["extra"]["alternate"]
    assert [entry["lang"] for entry in alternate] == ["en", *LANGUAGES]
    current = next(entry for entry in alternate if entry["lang"] == lang)
    assert current["link"] == "./"


# Settings documentation and anchors. Anchors are not checked by `--strict`, and a
# translated page that misses a new section or setting still builds.

FENCE = re.compile(r"^ *(```|~~~).*?^ *\1", re.MULTILINE | re.DOTALL)
HEADING = re.compile(r"^#{2,6} +(.+?)(?: +\{ *#([\w-]+) *\})? *$", re.MULTILINE)
EXPLICIT_ID = re.compile(r"\{[^}\n]*#([\w-]+)[^}\n]*\}")
LINK = re.compile(r"\]\(([^)\s]*)\)")
OVERVIEW_ROW = re.compile(r"^\| \[`([A-Z0-9_]+)`\]\(#([a-z0-9_]+)\) \| ([^|]+) \|", re.MULTILINE)


def docs_dir(lang: str) -> Path:
    return DOCS if lang == "en" else DOCS / lang


def prose(path: Path) -> str:
    """A page without its fenced code blocks, where `#` starts a comment."""
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def slugify(heading: str) -> str:
    """Python-Markdown's default toc slug, as Zensical generates it."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def heading_anchors(path: Path) -> list[str]:
    return [explicit or slugify(title) for title, explicit in HEADING.findall(prose(path))]


def anchors(path: Path) -> set[str]:
    """Every id a link can point at: headings and `{ #id }` on other elements."""
    return set(heading_anchors(path)) | set(EXPLICIT_ID.findall(prose(path)))


def overview(lang: str) -> list[tuple[str, str, str]]:
    """(variable, anchor, default) of every row of the overview table."""
    section = prose(docs_dir(lang) / "configuration.md").split("\n## ")[1]
    return OVERVIEW_ROW.findall(section)


@pytest.mark.parametrize("lang", ["en", *LANGUAGES])
def test_configuration_documents_every_setting(lang: str) -> None:
    rows = overview(lang)
    assert sorted(name for name, _, _ in rows) == sorted(
        name.upper() for name in Settings.model_fields
    )
    assert all(anchor == name.lower() for name, anchor, _ in rows)
    assert {anchor for _, anchor, _ in rows} <= anchors(docs_dir(lang) / "configuration.md")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_configuration_overview_matches_english(lang: str) -> None:
    # Defaults written as code are not translated; prose ones ("CPU count, max 4") are.
    def comparable(rows: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
        return [(name, default if default.startswith("`") else "") for name, _, default in rows]

    assert comparable(overview(lang)) == comparable(overview("en"))


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("page", sorted(markdown_pages(DOCS)))
def test_translation_has_every_section(lang: str, page: str) -> None:
    assert heading_anchors(DOCS / lang / page) == heading_anchors(DOCS / page)


@pytest.mark.parametrize("lang", ["en", *LANGUAGES])
def test_links_point_at_existing_anchors(lang: str) -> None:
    root = docs_dir(lang)
    broken = []
    for page in sorted(markdown_pages(DOCS)):
        source = root / page
        for target in LINK.findall(prose(source)):
            path, _, anchor = target.partition("#")
            if not anchor or "://" in path or not (path == "" or path.endswith(".md")):
                continue
            linked = (source.parent / path).resolve() if path else source
            if anchor not in anchors(linked):
                broken.append(f"{page} → {target}")
    assert broken == []
