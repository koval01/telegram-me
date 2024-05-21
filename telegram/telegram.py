"""
Telegram main module
"""

from typing import Literal

from telegram.parser import Body, More
from telegram.request import Request


class Telegram:
    """
    A class representing a simplified interface for
    interacting with the Telegram API.

    Methods:
        body: Retrieve message bodies from a Telegram channel.
        more: Retrieve additional messages from a Telegram channel.
    """

    def __init__(self) -> None:
        ...

    @staticmethod
    async def body(channel: str, position: int = 0) -> dict:
        """
        Retrieve the body of a message from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history.
            Defaults to 0 (most recent).

        Returns:
            dict: A dictionary containing the message body.
        """
        response = await Request().body(channel, position)
        if not response:
            return {}

        return Body(response).get()

    @staticmethod
    async def more(channel: str, position: int, direction: Literal["after", "before"]) -> dict:
        """
        Retrieve additional messages from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history.
            direction (Literal["after", "before"]):
            The direction of message retrieval relative to the
            specified position.

        Returns:
            dict: A dictionary containing the additional messages.
        """
        response = await Request().more(channel, position, direction)
        if not response:
            return {}

        return More(response).get()

    @staticmethod
    async def post(channel: str, position: int) -> dict:
        """
        Retrieve a specific post from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The ID of the post to retrieve.

        Returns:
            dict: A dictionary containing the requested post if found,
            otherwise an empty dictionary.
        """
        response = await Request().body(channel, position)
        if not response:
            return {}

        response = Body(response).get(position)
        if not response["content"]["posts"]:
            return {}

        return response
