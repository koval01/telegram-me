"""
Requests module
"""
import re
from typing import Literal, Union

import urllib.parse
import aiohttp

from app.utils.config import settings


class Request:
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
            Union[str, dict, None]: The response content,
            either as a string, dictionary (if JSON), or None.
        """

        sanitized_path: str = urllib.parse.quote(path)

        async with aiohttp.ClientSession() as session:
            async with session.request(
                    method=method,
                    url=f"https://{self.host}/{sanitized_path}",
                    allow_redirects=False,
                    params=params,
                    headers={
                        "X-Requested-With": "XMLHttpRequest"
                        if method == "POST" else "",
                        "User-Agent": f"TelegramMeAPI/{settings.VERSION} (https://github.com/koval01/telegram-me; yaroslav@koval.page)"  # pylint: disable=line-too-long
                    }
            ) as response:
                if response.status != 200:
                    return None

                if json:
                    return await response.json()

                return await response.text()

    @staticmethod
    def valid_channel(channel: str) -> bool:
        """
        Validates if the provided channel string is a valid channel name.

        Parameters:
        - channel (str): The channel name to be validated.

        Returns:
        - bool: True if the channel name is valid, False otherwise.
        """
        return bool(re.match(r"^[a-zA-Z0-9_-]{3,32}$", channel))

    @staticmethod
    def valid_position(position: int) -> bool:
        """
        Validates if the provided position is within the allowed range.

        Parameters:
        - position (int): The position value to be validated.

        Returns:
        - bool: True if the position is valid, False otherwise.
        """
        pattern = re.compile(r'^[0-9]{1,6}$')
        if re.match(pattern, str(position)) and 0 <= position <= 1000000:
            return True

        return False

    async def body(self, channel: str, position: int = 0) -> Union[str, None]:
        """
        Retrieves the body content of a channel at a given position.

        Args:
            channel (str): The channel identifier.
            position (int): The position to retrieve content from. Defaults to 0.

        Returns:
            Union[str, None]: The response body content, or None if request fails.
        """
        if not self.valid_channel(channel):
            return None

        if position and not self.valid_position(position):
            return None

        response = await self.__request(f"s/{channel}/{position}")
        return response if response else None

    async def more(
            self, channel: str, position: int, direction: Literal["before", "after"]
    ) -> Union[str, None]:
        """
        Retrieves more content from a channel relative to a given position.

        Args:
            channel (str): The channel identifier.
            position (int): The position to retrieve content relative to.
            direction (Literal["before", "after"]): The direction of content retrieval.

        Returns:
            Union[str, None]: Additional content in dictionary format, or None if request fails.
        """
        if not self.valid_channel(channel):
            return None

        response = await self.__request(
            f"s/{channel}",
            method="POST",
            json=True,
            params={direction: position}
        )
        return response if response else None

    async def preview(self, channel: str) -> Union[str, None]:
        """
        A method for obtaining preliminary information about a channel.

        Args:
            channel (str): The channel identifier.

        Returns:
            Union[str, None]: The response body content, or None if request fails.
        """
        if not self.valid_channel(channel):
            return None

        response = await self.__request(channel)
        return response if response else None
