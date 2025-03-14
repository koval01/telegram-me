"""Post module"""

import re
import json
from datetime import datetime

from typing import Union

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.telegram.parser.types.entities import EntitiesParser
from app.telegram.parser.types.media import Media
from app.telegram.parser.methods.utils import Utils


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
    def preview_link(buble: LexborNode) -> Union[dict, None]:
        """
        Extracts preview link information from a message bubble.

        Args:
            buble (LexborNode): The message bubble node containing the preview link.

        Returns:
            Union[dict, None]: A dictionary containing preview
                link information with the following keys:
                - site_name (str): Name of the linked site
                - title (str|None): Title of the preview, if present
                - description (dict|None): Description text with 'string'
                    and 'html' formats, if present
                - thumb (str|None): URL of the preview thumbnail image, if present
                Returns None if no preview link is found.
        """
        preview = buble.css_first(".tgme_widget_message_link_preview")
        if not preview:
            return None

        description = preview.css_first(".link_preview_description")
        if description:
            description = {
                "string": description.text(),
                "html": Utils.get_text_html(description)
            }

        site_name = preview.css_first(".link_preview_site_name")
        title = preview.css_first(".link_preview_title")
        thumb = preview.css_first(".tgme_widget_message_link_preview > .link_preview_right_image")

        return {
            "site_name": site_name.text(strip=True) if site_name else None,
            "url": preview.attributes.get("href"),
            "title": title.text(strip=True) if title else None,
            "description": description,
            "thumb": Utils.background_extr(
                thumb.attributes.get("style")
            ) if thumb else None,
        }

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
        meta = buble.css_first(".tgme_widget_message_meta")
        edited = meta.text().startswith("edited")
        author = cls.author(buble)

        footer = {
            "views": views.text() if views else None,
            "edited": edited,
            "date": {
                "string": time,
                "unix": cls.__unix_timestamp(time)
            }
        }

        if author:
            footer["author"] = author

        return footer

    @staticmethod
    def text(buble: LexborNode) -> Union[dict, None]:
        """
        Extracts text content from a message bubble.

        Args:
            buble (LexborNode): The message bubble node.

        Returns:
            Union[dict, None]: A dictionary containing text content, or None if no text found.
        """
        selector = buble.css_first(".tgme_widget_message_text, .js-message_text")
        if not selector:
            return None

        content = Utils.get_text_html(selector)

        delete_tags = ["a", "i", "b", "s", "u", "pre", "code", "span", "tg-emoji", "tg-spoiler"]
        selector.unwrap_tags(delete_tags)
        content_t = Utils.get_text_html(selector)
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
            "name": {
                "string": forwarded.text(),
                "html": Utils.get_text_html(forwarded, "span")
            }
        }
        if url:
            forwarded["url"] = url

        return forwarded

    @staticmethod
    def author(message: LexborNode) -> dict | None:
        """
        Extracts the author's name from a message node.

        Args:
            message (LexborNode): The message node.

        Returns:
            dict | None: The author's name as a string and html if present
        """
        auth = message.css_first(".tgme_widget_message_from_author")
        if not auth:
            return None

        return {
            "string": auth.text(),
            "html": Utils.get_text_html(auth, "span")
        }

    @staticmethod
    def reply(message: LexborNode) -> dict | None:
        """
        Extracts reply information from a message node.

        Args:
            message (LexborNode): The message node containing the reply.

        Returns:
            dict | None: A dictionary containing reply information or None if no reply is found.
        """
        reply = message.css_first(".tgme_widget_message_reply")
        if not reply:
            return None

        text = reply.css_first(
            ".tgme_widget_message_metatext, .tgme_widget_message_text"
        )
        if not text:
            return None

        cover = reply.css_first(".tgme_widget_message_reply_thumb")
        name = reply.css_first(".tgme_widget_message_author")
        return {
            "cover": Utils.background_extr(cover.attributes.get("style"))
                if cover else None,
            "name": {
                "string": name.text().strip(),
                "html": Utils.get_text_html(name, "span")
            },
            "text": {
                "string": text.text(),
                "html": Utils.get_text_html(text)
            },
            "to_message": int(re.search(
                r'https://t\.me/[\w-]+/(\d+)',
                reply.attributes.get("href")).group(1))
        }

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
                "inline": self.inline(message),
                "reply": self.reply(message),
                "preview_link": self.preview_link(message),
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
