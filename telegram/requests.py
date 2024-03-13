import aiohttp

from typing import Literal


class Requests:

    def __init__(self) -> None:
        self.host: str = "t.me"

    async def __request(
            self, path: str, method: Literal["GET", "POST"] = "GET", json: bool = False) -> str | dict | None:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                    method=method, url=f"https://{self.host}{path}", allow_redirects=False) as response:
                if response.status != 200:
                    return
                if json:
                    _json = await response.json()
                    return _json

                html = await response.text()
                return html

    async def body(self, channel: str, position: int = 0) -> str:
        response = await self.__request("/s/%s/%s" % (channel, position if position else ""))
        return response \
            if response else ""
