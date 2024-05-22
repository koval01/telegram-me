"""Body module"""

import json

from telegram.parser.parser import Parser
from telegram.parser.types.post import Post


class Body(Parser):
    """
    A class for extracting content from the body.

    Attributes:
        soup (LexborHTMLParser): An instance of LexborHTMLParser for parsing HTML content.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the Body object.

        Args:
            body (str): The HTML body content.
        """
        Parser.__init__(self, body)

    def get(self, selector: int | None = None) -> dict[str, dict]:
        """
        Extracts relevant information from the HTML body.

        Args:
            selector (int | None): ID message for select one.

        Returns:
            Dict[str, Dict[str, Dict[str, str]]]: A dictionary containing extracted content.
        """
        return {
            "channel": {
                "username": self.soup.css_first(".tgme_channel_info_header_username>a").text(),
                "title": self.get_meta("property", "og:title"),
                "description": self.get_meta("property", "og:description"),
                "avatar": self.get_meta("property", "og:image")
            },
            "content": {
                "counters": self.get_counters(
                    self.soup.css(".tgme_channel_info_counters>.tgme_channel_info_counter")),
                "posts": Post(self.soup.body.html).get(selector)
            },
            "meta": {
                "offset": self.get_offset(self.soup.head)
            }
        }

    def __str__(self) -> str:
        """
        Return representation for More object
        :return:
        """
        return json.dumps(self.get())
