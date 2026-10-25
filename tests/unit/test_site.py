"""The public pages: landing page, API reference, docs, robots.txt and sitemap.xml."""

import importlib.util
import json
import logging
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from telegram_me import __version__
from telegram_me.api import site
from tests.conftest import ClientFactory, make_settings

ROOT = Path(__file__).resolve().parents[2]
SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
XHTML = "{http://www.w3.org/1999/xhtml}"


def _load_build_docs() -> ModuleType:
    path = ROOT / "scripts" / "build_docs.py"
    spec = importlib.util.spec_from_file_location("build_docs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_docs = _load_build_docs()


@pytest.fixture
def docs_site(tmp_path: Path) -> Path:
    """A tiny documentation build with the layout scripts/build_docs.py produces."""
    files = {
        "index.html": "<title>TelegramMe docs</title>",
        "api/index.html": "<title>API overview</title>",
        "es/index.html": "<title>Documentación</title>",
        "404.html": "<title>Not found</title>",
        "assets/main.e442bf79.min.css": "body{}",
        "assets/images/favicon.png": "png",
        "sitemap.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>/docs/</loc></url><url><loc>/docs/api/</loc></url></urlset>"
        ),
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    manifest = {
        "prefix": "/docs/",
        "default": "en",
        "languages": {"en": "", "es": "es/"},
        "pages": [
            {"path": "", "languages": ["en", "es"]},
            {"path": "api/", "languages": ["en"]},
        ],
    }
    (tmp_path / "sitemap.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def sitemap_urls(xml: bytes) -> dict[str, dict[str, str]]:
    """`{loc: {hreflang: href}}` of a sitemap."""
    root = ET.fromstring(xml)  # noqa: S314 - the app's own output
    return {
        url.findtext(f"{SITEMAP}loc", ""): {
            link.attrib["hreflang"]: link.attrib["href"] for link in url.iter(f"{XHTML}link")
        }
        for url in root.iter(f"{SITEMAP}url")
    }


# Landing page ---------------------------------------------------------------------------


def test_landing_page(make_client: ClientFactory) -> None:
    response = make_client().get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    page = response.text
    assert f"v{__version__}" in page
    assert 'id="try-form"' in page
    assert 'href="/api"' in page
    assert 'href="/graphql"' in page
    assert f"releases/tag/v{__version__}" in page
    # Without a local build the docs links go to the published site.
    for lang in ("", "es/", "ru/"):
        assert f'href="{site.PUBLISHED_DOCS}{lang}"' in page
    assert f"/static/landing.js?v={site.ASSETS}" in page
    assert "{{" not in page


def test_landing_page_shows_the_instance_features(make_client: ClientFactory) -> None:
    client = make_client(enable_graphql=False, enable_feed=False, enable_rate_limit=True)
    page = client.get("/").text
    features = json.loads(
        page.split('data-features="', 1)[1].split('"', 1)[0].replace("&quot;", '"')
    )
    assert features == {
        "previews": True,
        "feed": False,
        "graphql": False,
        "rate_limit": True,
        "pow": False,
    }
    assert '<li hidden><a href="/graphql">' in page
    assert '<li data-state="off">Feed</li>' in page


def test_landing_page_links_local_docs(make_client: ClientFactory, docs_site: Path) -> None:
    page = make_client(docs_site_dir=docs_site).get("/").text
    for lang in ("", "es/", "ru/"):
        assert f'href="/docs/{lang}"' in page
    assert site.PUBLISHED_DOCS not in page


def test_pages_answer_head(make_client: ClientFactory) -> None:
    client = make_client()
    for path in ("/", "/api", "/robots.txt", "/sitemap.xml"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""


def test_unfilled_placeholder_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "page.html").write_text("<p>{{ known }} {{ unknown }}</p>", encoding="utf-8")
    monkeypatch.setattr(site, "TEMPLATES", tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        site._render("page.html", {"known": "yes"})


def test_static_files_are_cached_for_an_hour(make_client: ClientFactory) -> None:
    response = make_client().get(f"/static/landing.css?v={site.ASSETS}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"


# API reference --------------------------------------------------------------------------


def test_api_reference(make_client: ClientFactory) -> None:
    response = make_client().get("/api")
    assert response.status_code == 200
    page = response.text
    assert "SwaggerUIBundle" in page
    assert "url: '/openapi.json'" in page
    # The wrapper loads after the bundle and before the inline setup call.
    bundle = page.index("swagger-ui-bundle.js")
    wrapper = page.index(f"/static/api.js?v={site.ASSETS}")
    setup = page.index("SwaggerUIBundle({")
    assert bundle < wrapper < setup
    assert '<body data-pow="off" data-graphql="on">' in page


def test_api_reference_knows_about_proof_of_work(make_client: ClientFactory) -> None:
    client = make_client(enable_pow=True, pow_secret="x" * 32, enable_graphql=False)
    assert '<body data-pow="on" data-graphql="off">' in client.get("/api").text


def test_old_swagger_path_is_the_landing_page(make_client: ClientFactory) -> None:
    # Swagger's deep links (`/#/Channel/...`) are redirected to /api by landing.js.
    assert "location.hash" in make_client().get("/static/landing.js").text


# Documentation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/docs", site.PUBLISHED_DOCS),
        ("/docs/", site.PUBLISHED_DOCS),
        ("/docs/es/api/feed/", site.PUBLISHED_DOCS + "es/api/feed/"),
        ("/docs/configuration/?q=1", site.PUBLISHED_DOCS + "configuration/"),
    ],
)
def test_docs_redirect_to_the_published_site_without_a_build(
    make_client: ClientFactory, path: str, target: str
) -> None:
    response = make_client().get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == target


def test_docs_are_served_from_the_build(make_client: ClientFactory, docs_site: Path) -> None:
    client = make_client(docs_site_dir=docs_site)
    root = client.get("/docs", follow_redirects=False)
    assert root.status_code == 308
    assert root.headers["location"] == "/docs/"

    page = client.get("/docs/")
    assert page.status_code == 200
    assert "TelegramMe docs" in page.text
    assert page.headers["cache-control"] == "public, max-age=600"
    assert client.get("/docs/es/").text == "<title>Documentación</title>"

    asset = client.get("/docs/assets/main.e442bf79.min.css")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    image = client.get("/docs/assets/images/favicon.png")
    assert image.headers["cache-control"] == "public, max-age=600"

    missing = client.get("/docs/nope/")
    assert missing.status_code == 404
    assert "Not found" in missing.text


def test_docs_directory_redirect_is_relative(make_client: ClientFactory, docs_site: Path) -> None:
    # Behind a TLS proxy the app sees http://; the browser must stay on https://.
    response = make_client(docs_site_dir=docs_site).get(
        "/docs/api?x=1", follow_redirects=False, headers={"host": "tme.example.org"}
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/docs/api/?x=1"


def test_docs_keep_their_cache_control_at_the_edge(
    make_client: ClientFactory, docs_site: Path
) -> None:
    client = make_client(docs_site_dir=docs_site, cache_mode="normal")
    response = client.get("/docs/assets/main.e442bf79.min.css", headers={"CF-RAY": "abc"})
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    # Responses without their own still get the edge default.
    api = client.get("/v1/body/durov", headers={"CF-RAY": "abc"})
    assert api.headers["cache-control"] == "public, max-age=10, stale-while-revalidate=10"


def test_docs_site_dir_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="docs_site_dir"):
        make_settings(docs_site_dir=tmp_path / "missing")


# robots.txt and sitemap.xml -------------------------------------------------------------


def test_robots_txt(make_client: ClientFactory) -> None:
    response = make_client(public_url="https://tme.example.org").get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    lines = response.text.splitlines()
    assert lines[0] == "User-agent: *"
    assert {"Allow: /", "Disallow: /v1/", "Disallow: /graphql"} <= set(lines)
    assert lines[-1] == "Sitemap: https://tme.example.org/sitemap.xml"


def test_robots_txt_uses_the_request_url_without_public_url(make_client: ClientFactory) -> None:
    text = make_client().get("/robots.txt").text
    assert text.endswith("Sitemap: http://testserver/sitemap.xml\n")


def test_public_url_is_an_origin() -> None:
    assert make_settings(public_url="https://tme.example.org:8443").public_url
    for value in ("https://tme.example.org/", "tme.example.org", "https://a.org/docs"):
        with pytest.raises(ValidationError, match="public_url"):
            make_settings(public_url=value)


def test_sitemap_without_docs(make_client: ClientFactory) -> None:
    response = make_client(public_url="https://tme.example.org").get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert sitemap_urls(response.content) == {
        "https://tme.example.org/": {},
        "https://tme.example.org/api": {},
    }


def test_sitemap_lists_every_docs_page_with_its_translations(
    make_client: ClientFactory, docs_site: Path
) -> None:
    client = make_client(docs_site_dir=docs_site, public_url="https://tme.example.org")
    home = {
        "en": "https://tme.example.org/docs/",
        "es": "https://tme.example.org/docs/es/",
        "x-default": "https://tme.example.org/docs/",
    }
    assert sitemap_urls(client.get("/sitemap.xml").content) == {
        "https://tme.example.org/": {},
        "https://tme.example.org/api": {},
        "https://tme.example.org/docs/": home,
        "https://tme.example.org/docs/es/": home,
        "https://tme.example.org/docs/api/": {},
    }


def test_sitemap_follows_the_host_without_public_url(
    make_client: ClientFactory, docs_site: Path
) -> None:
    client = make_client(docs_site_dir=docs_site)
    urls = sitemap_urls(client.get("/sitemap.xml", headers={"host": "a.example"}).content)
    assert "http://a.example/docs/es/" in urls
    urls = sitemap_urls(client.get("/sitemap.xml", headers={"host": "b.example"}).content)
    assert "http://b.example/docs/es/" in urls


def test_sitemap_without_a_manifest_leaves_out_the_docs(
    make_client: ClientFactory, docs_site: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (docs_site / "sitemap.json").unlink()
    # Read directly: create_app configures logging and replaces caplog's handler.
    with caplog.at_level(logging.WARNING, logger="telegram_me.api.site"):
        assert site.docs_manifest(make_settings(docs_site_dir=docs_site))["pages"] == []
    assert "sitemap.json is missing" in caplog.text
    client = make_client(docs_site_dir=docs_site)
    assert len(sitemap_urls(client.get("/sitemap.xml").content)) == 2
    assert client.get("/docs/").status_code == 200


# DISABLE_DOCS ---------------------------------------------------------------------------


def test_disable_docs_hides_every_page(make_client: ClientFactory, docs_site: Path) -> None:
    client = make_client(disable_docs=True, docs_site_dir=docs_site)
    for path in ("/", "/api", "/openapi.json", "/sitemap.xml", "/docs", "/docs/", "/docs/es/"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.text == "User-agent: *\nDisallow: /\n"


# scripts/build_docs.py ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markdown", "url"),
    [
        ("index.md", ""),
        ("api/index.md", "api/"),
        ("api/feed.md", "api/feed/"),
        ("faq.md", "faq/"),
    ],
)
def test_build_docs_page_urls(markdown: str, url: str) -> None:
    assert build_docs.page_url(markdown) == url


@pytest.mark.parametrize("config", build_docs.CONFIGS)
def test_build_docs_moves_every_language_under_the_prefix(config: str) -> None:
    with (ROOT / config).open("rb") as file:
        project: dict[str, Any] = tomllib.load(file)["project"]
    root = build_docs.language_root(project)
    assert root == ("" if config == "zensical.toml" else f"{project['theme']['language']}/")
    out = ROOT / "site-instance"
    built = tomllib.loads(build_docs.instance_config(config, root, out))["project"]
    assert built["site_url"] == f"{build_docs.PLACEHOLDER}/docs/{root}"
    assert built["site_dir"] == f"site-instance/{root}".rstrip("/")
    # Everything else is the published build's configuration.
    for key in ("site_url", "site_dir"):
        built.pop(key)
        project.pop(key, None)
    assert built == project
    assert build_docs.nav_pages(project["nav"])[0] == "index.md"


def test_docs_sitemaps_have_absolute_urls(make_client: ClientFactory, docs_site: Path) -> None:
    client = make_client(docs_site_dir=docs_site, public_url="https://tme.example")
    response = client.get("/docs/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    root = ET.fromstring(response.content)  # noqa: S314 - the app's own output
    assert [url.findtext(f"{SITEMAP}loc") for url in root] == [
        "https://tme.example/docs/",
        "https://tme.example/docs/api/",
    ]
    # A language without its own sitemap file answers 404, not the docs' 404 page.
    assert client.get("/docs/es/sitemap.xml").status_code == 404
