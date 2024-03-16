import aiohttp
from typing import Literal, Union


class Requests:
    """
    A class for handling asynchronous HTTP requests.

    Attributes:
        host (str): The base host URL for the requests.
    """

    def __init__(self, host: str = "t.me") -> None:
        """
        Initializes the Requests object.

        Args:
            host (str): The base host URL for the requests. Defaults to "t.me".
        """
        self.host = host

    async def __request(
            self,
            path: str,
            method: Literal["GET", "POST"] = "GET",
            json: bool = False,
            params: dict = None
    ) -> Union[str, dict, None]:
        """
        Makes an asynchronous HTTP request.

        Args:
            path (str): The endpoint path for the request.
            method (Literal["GET", "POST"]): The HTTP method for the request. Defaults to "GET".
            json (bool): Whether the response is expected to be in JSON format. Defaults to False.
            params (dict): Additional parameters to be passed with the request. Defaults to None.

        Returns:
            Union[str, dict, None]: The response content, either as a string, dictionary (if JSON), or None.
        """
        async with aiohttp.ClientSession() as session:
            async with session.request(
                    method=method,
                    url=f"https://{self.host}{path}",
                    allow_redirects=False,
                    params=params,
                    headers={
                        "X-Requested-With": "XMLHttpRequest" if method == "POST" else ""
                    }
            ) as response:
                if response.status != 200:
                    return None
                if json:
                    return await response.json()
                else:
                    return await response.text()

    async def body(self, channel: str, position: int = 0) -> Union[str, None]:
        """
        Retrieves the body content of a channel at a given position.

        Args:
            channel (str): The channel identifier.
            position (int): The position to retrieve content from. Defaults to 0.

        Returns:
            Union[str, None]: The response body content, or None if request fails.
        """
        response = await self.__request("/s/%s/%d" % (channel, position))
        return response if response else None

    async def more(self, channel: str, position: int, direction: Literal["before", "after"]) -> Union[str, None]:
        """
        Retrieves more content from a channel relative to a given position.

        Args:
            channel (str): The channel identifier.
            position (int): The position to retrieve content relative to.
            direction (Literal["before", "after"]): The direction of content retrieval.

        Returns:
            Union[dict, None]: Additional content in dictionary format, or None if request fails.
        """
        response = await self.__request(
            f"/s/{channel}",
            method="POST",
            json=True,
            params={direction: position}
        )
        return response if response else None
