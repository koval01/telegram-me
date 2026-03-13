# pylint: disable=duplicate-code

import httpx
from httpx import AsyncClient

HTTP2_CLIENT_SETTINGS = {
    "http2": True,
    "timeout": 15.0,
    "limits": httpx.Limits(max_connections=200, max_keepalive_connections=50),
    "headers": {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "ru",
        "cache-control": "max-age=0",
        "connection": "keep-alive",
        "dnt": "1",
        "priority": "u=0, i",
        "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": "Android",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
    },
}

def get_global_client() -> AsyncClient:
    """Return shared HTTP2 client."""
    if not hasattr(get_global_client, "_client"):
        get_global_client._client = httpx.AsyncClient(**HTTP2_CLIENT_SETTINGS)  # type: ignore[attr-defined]
    return get_global_client._client  # type: ignore[attr-defined]
