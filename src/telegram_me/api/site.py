"""The instance's public face: landing page, API reference, docs, robots.txt, sitemap.xml.

Pages are rendered once per process from `templates/`; a request only copies
bytes. The documentation is the site `scripts/build_docs.py` builds for the
`/docs/` prefix (the image ships it, `DOCS_SITE_DIR`); without one, `/docs`
redirects to the published copy.
"""

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, override
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import URL
from starlette.types import Scope

from telegram_me import __version__
from telegram_me.settings import Settings

logger = logging.getLogger(__name__)

REPOSITORY = "https://github.com/koval01/telegram-me"
PUBLISHED_DOCS = "https://koval01.github.io/telegram-me/"
DOCS_PATH = "/docs"
API_REFERENCE_PATH = "/api"
PACKAGE = Path(__file__).resolve().parent.parent
TEMPLATES = PACKAGE / "templates"
STATIC_DIR = PACKAGE / "static"
# Built docs name bundled assets by content hash (`main.e442bf79.min.css`).
_HASHED = re.compile(r"\.[0-9a-f]{8}\.min\.(?:css|js)$")
_IMMUTABLE = "public, max-age=31536000, immutable"
_PAGE_CACHE = "public, max-age=300"
_TEXT_CACHE = "public, max-age=3600"
# The landing page loads nothing from elsewhere; channel avatars come from t.me's CDN.
_LANDING_CSP = (
    "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
_NOT_SERVED: dict[str, Any] = {"prefix": DOCS_PATH + "/", "languages": {}, "pages": []}


class CachedStaticFiles(StaticFiles):
    """Static files with a Cache-Control header: long for hashed names, short otherwise."""

    def __init__(self, *, directory: Path, html: bool = False, max_age: int) -> None:
        super().__init__(directory=directory, html=html)
        self._cache_control = f"public, max-age={max_age}"

    @override
    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        hashed = _HASHED.search(os.fspath(full_path))
        response.headers["Cache-Control"] = _IMMUTABLE if hashed else self._cache_control
        return response

    @override
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if isinstance(response, RedirectResponse):
            # `/docs/api` -> `/docs/api/` on the host and scheme the client used,
            # which behind a TLS proxy are not the ones this process sees.
            target = URL(response.headers["location"])
            query = f"?{target.query}" if target.query else ""
            response.headers["location"] = target.path + query
        return response


def docs_files(settings: Settings) -> CachedStaticFiles | None:
    """The built documentation to mount at `/docs`, when this instance serves it."""
    if settings.disable_docs or settings.docs_site_dir is None:
        return None
    return CachedStaticFiles(directory=settings.docs_site_dir, html=True, max_age=600)


def docs_manifest(settings: Settings) -> dict[str, Any]:
    """Pages and languages of the served docs (`sitemap.json` of the build)."""
    if settings.disable_docs or settings.docs_site_dir is None:
        return _NOT_SERVED
    path = settings.docs_site_dir / "sitemap.json"
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("%s is missing: /sitemap.xml leaves out the documentation", path)
        return _NOT_SERVED
    return manifest


def _asset_version() -> str:
    """A digest of the static files: `?v=` on their URLs, so a change is never served stale."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(STATIC_DIR).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSETS = _asset_version()


def _render(name: str, values: dict[str, str]) -> bytes:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{ " + key + " }}", value)
    if "{{ " in text:
        msg = f"{name}: unfilled placeholder near {text[text.index('{{ ') :][:40]!r}"
        raise ValueError(msg)
    return text.encode()


def _docs_links(local: bool) -> dict[str, str]:
    base = DOCS_PATH + "/" if local else PUBLISHED_DOCS
    return {"docs_en": base, "docs_es": base + "es/", "docs_ru": base + "ru/"}


def landing_page(settings: Settings) -> bytes:
    """The HTML of `/`."""
    features = {
        "previews": settings.enable_previews,
        "feed": settings.enable_feed,
        "graphql": settings.enable_graphql,
        "rate_limit": settings.enable_rate_limit,
        "pow": settings.enable_pow,
    }
    values = {
        "version": escape(__version__),
        "assets": ASSETS,
        "release": escape(f"{REPOSITORY}/releases/tag/v{__version__}"),
        "repository": REPOSITORY,
        "changelog": escape(f"{REPOSITORY}/blob/main/CHANGELOG.md"),
        "api_reference": API_REFERENCE_PATH,
        "graphql_hidden": "" if settings.enable_graphql else " hidden",
        "features": escape(json.dumps(features, separators=(",", ":"))),
        **_docs_links(settings.docs_site_dir is not None),
    }
    for name, enabled in features.items():
        values[f"{name}_state"] = "on" if enabled else "off"
    return _render("landing.html", values)


def api_reference_page(settings: Settings) -> bytes:
    """Swagger UI for `/openapi.json`, solving proof of work when the server asks for it."""
    swagger = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="TelegramMe API reference",
        swagger_favicon_url=f"/static/favicon.svg?v={ASSETS}",
    )
    html = bytes(swagger.body).decode()
    bundle = html.index("</script>") + len("</script>")
    # Runs after the bundle and before the inline `SwaggerUIBundle({...})` call.
    extra = f'\n<script src="/static/api.js?v={ASSETS}"></script>'
    page = html[:bundle] + extra + html[bundle:]
    pow_ = "on" if settings.enable_pow else "off"
    graphql = "on" if settings.enable_graphql else "off"
    page = page.replace("<body>", f'<body data-pow="{pow_}" data-graphql="{graphql}">', 1)
    return page.encode()


def robots_txt(base: str, settings: Settings) -> str:
    """Crawl the pages and the docs, never the API."""
    if settings.disable_docs:
        return "User-agent: *\nDisallow: /\n"
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /v1/\n"
        "Disallow: /graphql\n"
        f"\nSitemap: {base}/sitemap.xml\n"
    )


def sitemap_xml(base: str, manifest: dict[str, Any]) -> bytes:
    """The landing page, the API reference and every docs page with its translations."""
    urls = [_url(base + "/", []), _url(base + API_REFERENCE_PATH, [])]
    prefix: str = manifest["prefix"]
    roots: dict[str, str] = manifest["languages"]
    for page in manifest["pages"]:
        path: str = page["path"]
        versions = {lang: f"{base}{prefix}{roots[lang]}{path}" for lang in page["languages"]}
        alternates = list(versions.items()) if len(versions) > 1 else []
        if alternates and manifest.get("default") in versions:
            alternates.append(("x-default", versions[manifest["default"]]))
        urls.extend(_url(loc, alternates) for loc in versions.values())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(urls) + "\n</urlset>\n"
    ).encode()


def _url(loc: str, alternates: list[tuple[str, str]]) -> str:
    links = "".join(
        f'\n    <xhtml:link rel="alternate" hreflang={quoteattr(lang)} href={quoteattr(href)}/>'
        for lang, href in alternates
    )
    return f"  <url>\n    <loc>{xml_escape(loc)}</loc>{links}\n  </url>"


def create_router(settings: Settings) -> APIRouter:
    """Routes of the public pages; the docs files themselves are mounted by the app."""
    router = APIRouter(include_in_schema=False)

    def base_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    @router.api_route("/robots.txt", methods=["GET", "HEAD"])
    async def robots(request: Request) -> Response:
        return Response(
            robots_txt(base_url(request), settings),
            media_type="text/plain",
            headers={"Cache-Control": _TEXT_CACHE},
        )

    if settings.disable_docs:
        return router

    landing = landing_page(settings)
    reference = api_reference_page(settings)
    manifest = docs_manifest(settings)

    # Keyed by base URL, which comes from the Host header without PUBLIC_URL: bounded.
    @lru_cache(maxsize=8)
    def sitemap(base: str) -> bytes:
        return sitemap_xml(base, manifest)

    @router.api_route("/", methods=["GET", "HEAD"])
    async def home() -> Response:
        return Response(
            landing,
            media_type="text/html",
            headers={"Cache-Control": _PAGE_CACHE, "Content-Security-Policy": _LANDING_CSP},
        )

    @router.api_route(API_REFERENCE_PATH, methods=["GET", "HEAD"])
    async def api_reference() -> Response:
        return Response(reference, media_type="text/html", headers={"Cache-Control": _PAGE_CACHE})

    @router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    async def sitemap_route(request: Request) -> Response:
        return Response(
            sitemap(base_url(request)),
            media_type="application/xml",
            headers={"Cache-Control": _TEXT_CACHE},
        )

    if docs_files(settings) is not None:

        @router.api_route(DOCS_PATH, methods=["GET", "HEAD"])
        async def docs_root() -> RedirectResponse:
            return RedirectResponse(DOCS_PATH + "/", status_code=308)

    else:
        # Temporary: the instance may serve the docs itself after its next upgrade.
        @router.api_route(DOCS_PATH, methods=["GET", "HEAD"])
        @router.api_route(DOCS_PATH + "/{path:path}", methods=["GET", "HEAD"])
        async def published_docs(request: Request) -> RedirectResponse:
            page = request.url.path.removeprefix(DOCS_PATH).lstrip("/")
            return RedirectResponse(PUBLISHED_DOCS + page, status_code=302)

    return router
