#!/usr/bin/env python3
"""Build the documentation as the instance serves it, under `/docs/`.

    uv run --group docs python scripts/build_docs.py [--out site-instance]

GitHub Pages serves the same pages from `zensical*.toml` at its own URL. Here
every language is built again with `site_url` under `/docs/`, so links,
assets, the 404 page and the language switcher work below that prefix. The
host is not known until the image runs, so the build uses a placeholder one
and then drops it: canonical links become root-relative (`/docs/...`) and
point at whichever instance serves them. The theme reads each language's
`sitemap.xml` (as root-relative paths too) to navigate between pages.

Also writes `sitemap.json` from the navs: every page and the languages that
have it. The app turns it into `/sitemap.xml` with its public URL
(`PUBLIC_URL`), the one robots.txt points crawlers at.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# English first: its build cleans the whole output directory.
CONFIGS = ("zensical.toml", "zensical.es.toml", "zensical.ru.toml")
PREFIX = "/docs/"
PLACEHOLDER = "https://instance.invalid"


def nav_pages(nav: list[Any]) -> list[str]:
    """Markdown paths of a nav in order."""
    pages: list[str] = []
    for item in nav:
        if isinstance(item, str):
            pages.append(item)
            continue
        for value in item.values():
            pages.extend(nav_pages(value) if isinstance(value, list) else [value])
    return pages


def page_url(markdown: str) -> str:
    """URL of a page relative to its language root (directory URLs)."""
    path = markdown.removesuffix(".md")
    if path == "index":
        return ""
    return path.removesuffix("/index") + "/"


def language_root(project: dict[str, Any]) -> str:
    """Where a build lands below the docs root: `""` for English, `"es/"`, ..."""
    site_dir = Path(project.get("site_dir", "site"))
    relative = site_dir.relative_to("site").as_posix()
    return "" if relative == "." else relative + "/"


def instance_config(source: str, root: str, out: Path) -> str:
    """`source` with the site URL and output directory of the instance build."""
    text = (ROOT / source).read_text(encoding="utf-8")
    site_url = f'site_url = "{PLACEHOLDER}{PREFIX}{root}"'
    site_dir = f'site_dir = "{(out / root).relative_to(ROOT).as_posix().rstrip("/")}"'
    text, urls = re.subn(r"^site_url = .*$", site_url, text, count=1, flags=re.MULTILINE)
    if urls != 1:
        msg = f"{source}: no site_url"
        raise SystemExit(msg)
    text, dirs = re.subn(r"^site_dir = .*$", site_dir, text, count=1, flags=re.MULTILINE)
    if dirs == 0:
        text = text.replace(site_url, f"{site_url}\n{site_dir}", 1)
    return text


def build(out: Path) -> dict[str, Any]:
    """Build every language into `out`; return the sitemap manifest."""
    languages: dict[str, str] = {}
    pages: dict[str, list[str]] = {}
    for source in CONFIGS:
        with (ROOT / source).open("rb") as file:
            project: dict[str, Any] = tomllib.load(file)["project"]
        language: str = project["theme"]["language"]
        root = language_root(project)
        languages[language] = root
        for markdown in nav_pages(project["nav"]):
            pages.setdefault(page_url(markdown), []).append(language)

        config = ROOT / f".zensical-instance.{language}.toml"
        config.write_text(instance_config(source, root, out), encoding="utf-8")
        try:
            subprocess.run(  # noqa: S603 - fixed command line
                ["zensical", "build", "--clean", "--strict", "-f", config.name],  # noqa: S607
                cwd=ROOT,
                check=True,
            )
        finally:
            config.unlink()

    for page in [*out.rglob("*.html"), *out.rglob("sitemap.xml")]:
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace(PLACEHOLDER, ""), encoding="utf-8")
    for root in languages.values():
        (out / root / "sitemap.xml.gz").unlink(missing_ok=True)
    leftovers = [
        path.relative_to(out).as_posix()
        for path in out.rglob("*")
        if path.is_file() and PLACEHOLDER.encode() in path.read_bytes()
    ]
    if leftovers:
        msg = f"placeholder host left in {', '.join(leftovers)}"
        raise SystemExit(msg)
    return {
        "prefix": PREFIX,
        "default": next(iter(languages)),
        "languages": languages,
        "pages": [{"path": path, "languages": langs} for path, langs in pages.items()],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=ROOT / "site-instance")
    args = parser.parse_args()
    out: Path = args.out.resolve()
    if not out.is_relative_to(ROOT):
        print(f"--out must be inside {ROOT} (zensical requires it)", file=sys.stderr)
        return 2
    if out.exists():
        shutil.rmtree(out)
    manifest = build(out)
    (out / "sitemap.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{len(manifest['pages'])} pages in {', '.join(manifest['languages'])} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
