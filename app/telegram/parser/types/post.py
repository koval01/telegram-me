"""Post module"""

import re
import json
from datetime import datetime

from typing import Union

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.telegram.parser.types.entities import EntitiesParser
from app.telegram.parser.types.media import Media


class Post:
    """
    A class for parsing and extracting message information from posts within HTML content.

    Attributes:
        soup (LexborHTMLParser): An instance of LexborHTMLParser for parsing HTML content.
        buble (function): A lambda function for selecting message bubbles.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the Post object.

        Args:
            body (str): The HTML body content.
        """
        self.soup = LexborHTMLParser(body)
        self.buble = lambda m: m.css_first(".tgme_widget_message_bubble")

    @staticmethod
    def __unix_timestamp(timestamp: str) -> int:
        """
        Converts a timestamp string to UNIX timestamp format.

        Args:
            timestamp (str): The timestamp string in the format "%Y-%m-%dT%H:%M:%S%z".

        Returns:
            int: The UNIX timestamp.
        """
        timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
        return int(timestamp.timestamp())

    def messages(self) -> list[LexborNode]:
        """
        Retrieves message nodes from the HTML content.

        Returns:
            List[LexborNode]: A list of message nodes.
        """
        return self.soup.css(".tgme_widget_message_wrap > .tgme_widget_message")

    @staticmethod
    def poll(buble: LexborNode) -> Union[dict, None]:
        """
        Extracts poll information from a message bubble.

        Args:
            buble (LexborNode): The message bubble node.

        Returns:
            Union[dict, None]: A dictionary containing poll information,
            or None if no poll found.
        """
        poll = buble.css_first(".tgme_widget_message_poll")
        if not poll:
            return None

        _type = poll.css_first(".tgme_widget_message_poll_type")
        options = poll.css(".tgme_widget_message_poll_option")
        return {
            "question": poll.css_first(".tgme_widget_message_poll_question").text(),
            "type": _type.text() if _type else None,
            "votes": buble.css_first(".tgme_widget_message_voters").text(),
            "options": [
                {
                    "name": o.css_first(".tgme_widget_message_poll_option_text").text(),
                    "percent": int(o.css_first(".tgme_widget_message_poll_option_percent")
                                   .text()[:-1])
                }
                for o in options
            ]
        }

    @classmethod
    def footer(cls, buble: LexborNode) -> dict:
        """
        Extracts footer information from a message bubble.

        Args:
            buble (LexborNode): The message bubble node.

        Returns:
            dict: A dictionary containing footer information.
        """
        time = (buble.css_first(".tgme_widget_message_date > time")
                .attributes.get("datetime"))
        views = buble.css_first(".tgme_widget_message_views")
        author = cls.author(buble)

        footer = {
            "views": views.text() if views else None,
            "date": {
                "string": time,
                "unix": cls.__unix_timestamp(time)
            }
        }

        if author:
            footer["author"] = author

        return footer

    @staticmethod
    def get_text_html(selector: LexborNode) -> str | None:
        """
        Extracts and returns the inner HTML content of the first <div> element found in the HTML
        represented by the LexborNode object.

        Args:
            selector (LexborNode): A LexborNode object containing the HTML content to be searched.

        Returns:
            Optional[str]: The inner HTML content of the first <div> element,
                if found; otherwise, `None`.
        """
        match = re.match(r"<div.*?>(.*)</div>", selector.html, flags=re.M)
        if match:
            return match.group(1)

        return None

    def text(self, buble: LexborNode) -> Union[dict, None]:
        """
        Extracts text content from a message bubble.

        Args:
            buble (LexborNode): The message bubble node.

        Returns:
            Union[dict, None]: A dictionary containing text content, or None if no text found.
        """
        selector = buble.css_first(".tgme_widget_message_text")
        if not selector:
            return None

        content = self.get_text_html(selector)

        delete_tags = ["a", "i", "b", "s", "u", "pre", "code", "span", "tg-emoji", "tg-spoiler"]
        selector.unwrap_tags(delete_tags)
        content_t = self.get_text_html(selector)
        text = re.sub(r"<br\s?/?>", "\n", content_t)

        div = re.compile(
            r'<div+\sclass="tgme_widget_message_text.*"+\sdir="auto">(.*?)</div>',
            flags=re.DOTALL)
        div_match = re.search(div, text)
        text = div_match.group(1) if div_match else text
        text = text.replace("&nbsp;", " ")

        entities = EntitiesParser(content).parse_message()
        response = {
            "string": text,
            "html": content
        }
        if entities:
            response["entities"] = entities

        return response

    @staticmethod
    def inline(message: LexborNode) -> Union[list, None]:
        """
        Extracts inline buttons from a Telegram message.

        Args:
            message (LexborNode): The message node containing inline buttons.

        Returns:
            Union[list, None]: A list of dictionaries representing each inline button,
            where each dictionary contains 'title' as the button title and 'url' as the URL
            associated with the button. Returns None if no inline buttons are found.
        """

        selector = message.css_first(".tgme_widget_message_inline_row")
        if not selector:
            return None

        return [
            {
                "title": inline.css_first("span").text(),
                "url": inline.attributes.get("href")
            }
            for inline in
            selector.css(".tgme_widget_message_inline_button")
        ]

    @staticmethod
    def post_id(message: LexborNode) -> int:
        """
        Extracts the post ID from a message node.

        Args:
            message (LexborNode): The message node.

        Returns:
            int: The post ID.
        """
        selector = message.attributes.get('data-post')
        return int(selector.split("/")[1])

    @staticmethod
    def forwarded(message: LexborNode) -> Union[dict, None]:
        """
        Extracts forwarded information from a message node.

        Args:
            message (LexborNode): The message node.

        Returns:
            Union[dict, None]: A dictionary containing forwarded information,
            or None if not forwarded.
        """
        forwarded = message.css_first(".tgme_widget_message_forwarded_from_name")
        if not forwarded:
            return None

        url = forwarded.attributes.get("href")
        forwarded = {
            "name": forwarded.text()
        }
        if url:
            forwarded["url"] = url

        return forwarded

    @staticmethod
    def author(message: LexborNode) -> str | None:
        """
        Extracts the author's name from a message node.

        Args:
            message (LexborNode): The message node.

        Returns:
            str | None: The author's name as a string if present,
            or None if the author information is not found.
        """
        auth = message.css_first(".tgme_widget_message_from_author")
        if not auth:
            return None

        return auth.text()

    def get(self, selector: int | None = None) -> list[dict]:
        """
        Extracts post information from the HTML content.

        Args:
            selector (int | None): ID message for select one message.

        Returns:
            List[dict]: A list of dictionaries containing post information.
        """
        posts = []
        for message in self.messages():
            identifier = self.post_id(message)
            if (identifier and selector) and selector != identifier:
                continue

            buble = self.buble(message)

            content = {}
            content_fields = {
                "text": self.text(buble),
                "media": Media(buble).extract_media(),
                "poll": self.poll(buble),
                "inline": self.inline(message)
            }
            for k, v in content_fields.items():
                if v:
                    content[k] = v

            if not content:
                # skip if no content
                continue

            post = {
                "id": identifier,
                "content": content,
                "footer": self.footer(buble),
                "view": message.attributes.get("data-view")
            }
            forwarded = self.forwarded(message)
            if forwarded:
                post["forwarded"] = forwarded

            posts.append(post)

        return posts

    def __str__(self) -> str:
        """
        Return representation for Post object
        """
        return json.dumps(self.get())
