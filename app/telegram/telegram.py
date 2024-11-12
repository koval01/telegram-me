"""
Telegram main module
"""

from typing import Literal

from app.telegram.parser.methods.body import Body
from app.telegram.parser.methods.more import More
from app.telegram.parser.methods.preview import Preview

from app.telegram.request import Request


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

        # additional validation response
        response = More(response).get()
        response["posts"] = list(filter(lambda post: post["id"] != position, response["posts"]))
        return response

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

    @staticmethod
    async def preview(channel: str) -> dict:
        """
        Retrieve preview information of channel.

        Args:
            channel (str): The channel name or ID.

        Returns:
            dict: A dictionary containing the main channel information.
        """
        response = await Request().preview(channel)
        if not response:
            return {}

        return Preview(response).get()
