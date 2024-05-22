"""More module"""

import json

from telegram.parser.parser import Parser
from telegram.parser.types.post import Post


class More(Parser):
    """
    A class for extracting additional content from channel.

    Attributes:
        soup (LexborHTMLParser): An instance of LexborHTMLParser for parsing HTML content.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the More object.

        Args:
            body (str): The HTML body content.
        """
        Parser.__init__(self, body)

    def get(self) -> dict[str, list[dict] | list | dict[str, list[dict[str, int]]]]:
        """
        Extracts additional content from the HTML body.

        Returns:
            Dict[str, Dict[str, Dict[str, str]]]: A dictionary containing extracted content.
        """
        return {
            "posts": Post(self.soup.body.html).get(),
            "meta": {
                "offset": self.get_offset(self.soup.body, more=True)
            }
        }

    def __str__(self) -> str:
        """
        Return representation for More object
        """
        return json.dumps(self.get())
