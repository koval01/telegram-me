"""Channel module"""

import json

from selectolax.lexbor import LexborHTMLParser


class Channel:
    """
    Represents a Telegram channel parsed from HTML content.

    Attributes:
        soup (LexborHTMLParser): The parsed HTML content.
        page (Node): The main node representing the channel page in the HTML content.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the Channel instance by parsing the provided HTML content.

        Args:
            body (str): The HTML content of the Telegram channel page.
        """
        self.soup = LexborHTMLParser(body)
        self.page = self.soup.css_first(".tgme_page")

    def is_channel(self) -> bool:
        """
        Checks if the parsed HTML content represents a Telegram channel.

        Returns:
            bool: True if the HTML content represents a Telegram channel, False otherwise.
        """
        context_link = self.page.css_first(".tgme_page_context_link")
        if not context_link:
            return False

        is_channel = context_link.text() == "Preview channel"
        if not is_channel:
            return False

        return True

    def get(self) -> dict | None:
        """
        Extracts and returns channel information if the HTML content represents a Telegram channel.

        Returns:
            dict | None: A dictionary containing channel information
                        (title, subscribers, description, is_verified)
                         if the HTML content represents a Telegram channel, None otherwise.
        """
        if not self.is_channel():
            return None

        resp = {
            "title": self.page.css_first(".tgme_page_title>span").text(),
            "subscribers": self.page.css_first(".tgme_page_extra").text(),
            "is_verified": self.page.css_first("i.verified-icon") is not None
        }

        description = self.page.css_first(".tgme_page_description")
        if description:
            resp["description"] = description.text()

        avatar = self.page.css_first("img.tgme_page_photo_image")
        if avatar:
            resp["avatar"] = avatar.attributes.get("src")

        return resp

    def __str__(self) -> str:
        """
        Returns a JSON string representation of the channel information.

        Returns:
            str: A JSON string representing the channel information.
        """
        return json.dumps(self.get())
