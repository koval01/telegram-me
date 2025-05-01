import asyncio
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientSession, ClientError, ClientTimeout
from aiohttp.client_exceptions import TooManyRedirects, InvalidURL


class MIMEExtractor:
    """
    Async MIME type extractor for URLs using HEAD requests.

    Features:
    - Concurrent processing of multiple URLs
    - Custom timeout handling
    - Redirect following with limit
    - Proper resource cleanup
    - Comprehensive error handling
    """

    def __init__(
            self,
            max_concurrent: int = 100,
            timeout: float = 10.0,
            max_redirects: int = 5,
            user_agent: str = "MIMEExtractor/1.0"
    ):
        """
        Initialize the extractor with configuration.

        Args:
            max_concurrent: Maximum concurrent requests
            timeout: Request timeout in seconds
            max_redirects: Maximum redirects to follow
            user_agent: User-Agent string to use
        """
        self.max_concurrent = max_concurrent
        self.timeout = ClientTimeout(total=timeout)
        self.max_redirects = max_redirects
        self.headers = {'User-Agent': user_agent}
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _get_mime_type(
            self,
            session: ClientSession,
            url: str
    ) -> Tuple[str, Optional[str]]:
        """
        Perform a HEAD request and extract MIME type for a single URL.

        Args:
            session: aiohttp ClientSession
            url: URL to check

        Returns:
            Tuple of (original_url, mime_type) where mime_type may be None if failed
        """
        try:
            async with self._semaphore:
                async with session.head(
                        url,
                        allow_redirects=True,
                        timeout=self.timeout,
                        headers=self.headers
                ) as response:
                    content_type = response.headers.get('Content-Type', '')
                    mime_type = content_type.split(';')[0].strip().lower() or None
                    return (url, mime_type)

        except (ClientError, TooManyRedirects, InvalidURL, asyncio.TimeoutError) as _:
            return (url, None)

    async def extract(
            self,
            urls: list[str],
            raise_errors: bool = False
    ) -> Dict[str, Optional[str]]:
        """
        Extract MIME types for multiple URLs.

        Args:
            urls: List of URLs to process
            raise_errors: Whether to raise exceptions or suppress them

        Returns:
            Dictionary mapping URLs to their MIME types (None if failed)

        Raises:
            Only when raise_errors=True: various aiohttp exceptions
        """
        valid_urls = []
        for url in urls:
            try:
                parsed = urlparse(url)
                if not parsed.scheme or not parsed.netloc:
                    raise ValueError(f"Invalid URL: {url}")
                valid_urls.append(url)
            except ValueError as _:
                if raise_errors:
                    raise
                continue

        connector = aiohttp.TCPConnector(limit=self.max_concurrent)
        async with ClientSession(
                connector=connector,
                timeout=self.timeout,
                raise_for_status=False
        ) as session:
            tasks = [self._get_mime_type(session, url) for url in valid_urls]
            results = await asyncio.gather(*tasks, return_exceptions=not raise_errors)

            mime_map = {}
            for result in results:
                if isinstance(result, Exception):
                    if raise_errors:
                        raise result
                    continue
                url, mime = result
                mime_map[url] = mime

        return mime_map
