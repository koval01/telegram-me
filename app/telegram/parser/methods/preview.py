"""Preview module"""

import json

from app.telegram.parser.parser import Parser
from app.telegram.parser.types.channel import Channel


class Preview(Parser):
    """
    Represents a preview of a Telegram channel.

    Inherits from the Parser class and provides functionality to parse
    and extract information from the preview of a Telegram channel.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the Preview instance by parsing the provided HTML content.

        Args:
            body (str): The HTML content of the preview page.
        """
        Parser.__init__(self, body)

    def get(self) -> dict | None:
        """
        Extracts and returns preview information of the Telegram channel.

        Returns:
            dict | None: A dictionary containing the channel information
                         if the preview is valid, None otherwise.
        """
        channel = Channel(self.soup.body.html).get()
        if not channel:
            return None

        return {
            "channel": channel
        }

    def __str__(self) -> str:
        """
        Returns a JSON string representation of the preview information.

        Returns:
            str: A JSON string representing the preview information.
        """
        return json.dumps(self.get())
