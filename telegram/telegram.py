from typing import Literal

from telegram.requests import Requests
from telegram.parser import Body, More


class Telegram:
    """
    A class representing a simplified interface for interacting with the Telegram API.

    Methods:
        body: Retrieve message bodies from a Telegram channel.
        more: Retrieve additional messages from a Telegram channel.
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    async def body(channel: str, position: int = 0) -> dict:
        """
        Retrieve the body of a message from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history. Defaults to 0 (most recent).

        Returns:
            dict: A dictionary containing the message body.
        """
        response = await Requests().body(channel, position)
        if not response:
            return {}

        return await Body(response).get()

    @staticmethod
    async def more(channel: str, position: int, direction: Literal["after", "before"]) -> dict:
        """
        Retrieve additional messages from a Telegram channel.

        Args:
            channel (str): The channel name or ID.
            position (int): The position of the message in the channel's history.
            direction (Literal["after", "before"]): The direction of message retrieval relative to the
            specified position.

        Returns:
            dict: A dictionary containing the additional messages.
        """
        response = await Requests().more(channel, position, direction)
        if not response:
            return {}

        return await More(response).get()
