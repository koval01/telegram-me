"""
Main application module for the TelegramMe API.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.responses import HTMLResponse

from routes import body, more, healthz
from middleware import proxy_cache_header_middleware, process_time_middleware

from version import Version
from misc import open_api

app = FastAPI(
    title="TelegramMe API",
    description="API implementation of Telegram channel viewer in python",
    docs_url=None,
    version=Version().hex
)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def custom_swagger_ui_html() -> HTMLResponse:
    """
    This function returns custom Swagger UI HTML for the FastAPI application.

    Returns:
        HTMLResponse: An HTML response containing custom Swagger UI HTML.
    """
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title,
        swagger_css_url="/static/swagger/css/swagger-ui.css",
        swagger_js_url="/static/swagger/js/swagger-ui-bundle.js",
        swagger_favicon_url="/static/swagger/icons/favicon.png"
    )


app.include_router(body.router)
app.include_router(more.router)
app.include_router(healthz.router)

app.middleware("http")(proxy_cache_header_middleware)
app.middleware("http")(process_time_middleware)

if __name__ == "__main__":
    open_api(app)
