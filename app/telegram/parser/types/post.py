import re
import json
from datetime import datetime

from typing import Union, Optional, Dict, List, Any, Tuple

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
        return self.soup.css(
            ".tgme_widget_message_wrap > .tgme_widget_message"
        )

    @staticmethod
    def __extract_media_info(buble: LexborNode) -> Optional[List[Dict]]:
        """
        Extracts media information from a message bubble.

        Args:
            buble (LexborNode): The message bubble node.

        Returns:
            Optional[Dict[str, Any]]: Media information dictionary or None if no media.
        """
        return Media(buble).extract_media()

    @staticmethod
    def __parse_content_fields(
        post_instance, buble: LexborNode, message: LexborNode
    ) -> Dict[str, Any]:
        """
        Parses all content fields from a message.

        Args:
            post_instance: The Post instance.
            buble (LexborNode): The message bubble node.
            message (LexborNode): The message node.

        Returns:
            Dict[str, Any]: Dictionary of content fields that are not None.
        """
        content_fields = {
            "text": post_instance.text(buble),
            "media": Post.__extract_media_info(buble),
            "poll": post_instance.poll(buble),
            "inline": post_instance.inline(message),
            "reply": post_instance.reply(message),
            "preview_link": post_instance.preview_link(message),
            "reacts": post_instance.reactions(message)
        }

        return {k: v for k, v in content_fields.items() if v}

    @staticmethod
    def __clean_html_text(html_content: str) -> str:
        """
        Cleans HTML content to extract plain text.

        Args:
            html_content (str): The HTML content to clean.

        Returns:
            str: Cleaned text content.
        """
        # Replace <br> tags with newlines
        text = re.sub(r"<br\s?/?>", "\n", html_content)

        # Extract content from div if present
        div = re.compile(
            r'<div\s+class="tgme_widget_message_text(?:[^"]*)"(?:\s+[^>]*?)?>(.*?)(?:</div>|$)',
            flags=re.DOTALL,
        )
        div_match = re.search(div, text)
        text = div_match.group(1) if div_match else text

        # Replace HTML entities
        text = text.replace("&nbsp;", " ")

        return text

    @staticmethod
    def __extract_reply_data(
        reply: LexborNode,
    ) -> Tuple[
        Optional[LexborNode], Optional[LexborNode], Optional[LexborNode]
    ]:
        """
        Extracts various components from a reply node.

        Args:
            reply (LexborNode): The reply node.

        Returns:
            Tuple[Optional[LexborNode], Optional[LexborNode], Optional[LexborNode]]:
                Tuple of (cover, name, text) nodes.
        """
        cover = reply.css_first(".tgme_widget_message_reply_thumb")
        name = reply.css_first(".tgme_widget_message_author")
        text = reply.css_first(
            ".tgme_widget_message_metatext, .tgme_widget_message_text"
        )

        return cover, name, text

    @staticmethod
    def __extract_preview_site_name(preview: LexborNode) -> Optional[str]:
        """Extracts site name from preview node."""
        site_name = preview.css_first(".link_preview_site_name")
        return site_name.text(strip=True) if site_name else None

    @staticmethod
    def __extract_preview_title(preview: LexborNode) -> Optional[str]:
        """Extracts title from preview node."""
        title = preview.css_first(".link_preview_title")
        return title.text(strip=True) if title else None

    @staticmethod
    def __extract_preview_thumb(preview: LexborNode) -> Optional[str]:
        """Extracts thumb from preview node."""
        thumb = preview.css_first(
            ".tgme_widget_message_link_preview > .link_preview_right_image"
        )
        return (
            Utils.background_extr(thumb.attributes.get("style"))
            if thumb and thumb.attributes.get("style")
            else None
        )

    @staticmethod
    def preview_link(buble: LexborNode) -> Union[dict, None]:
        """
        Extracts preview link information from a message bubble.

        Args:
            buble (LexborNode): The message bubble node containing the preview link.

        Returns:
            Union[dict, None]: A dictionary containing preview link information
                or None if not found.
        """
        preview = buble.css_first(".tgme_widget_message_link_preview")
        if not preview:
            return None

        return {
            "site_name": Post.__extract_preview_site_name(preview),
            "url": preview.attributes.get("href"),
            "title": Post.__extract_preview_title(preview),
            "description": Post.__extract_preview_description(preview),
            "thumb": Post.__extract_preview_thumb(preview),
        }

    @staticmethod
    def __extract_preview_description(
        preview: LexborNode,
    ) -> Optional[Dict[str, str]]:
        """Extracts and formats the description from the preview node."""
        description = preview.css_first(".link_preview_description")
        if not description:
            return None

        return {
            "string": description.text(),
            "html": Utils.get_text_html(description),
        }

    @staticmethod
    def __extract_poll_question(poll: LexborNode) -> Optional[str]:
        """Extracts poll question from poll node."""
        question = poll.css_first(".tgme_widget_message_poll_question")
        return question.text() if question else None

    @staticmethod
    def __extract_poll_options(poll: LexborNode) -> List[Dict[str, Any]]:
        """Extracts poll options from poll node."""
        options = poll.css(".tgme_widget_message_poll_option")
        return [
            {
                "name": option.css_first(
                    ".tgme_widget_message_poll_option_text"
                ).text(),
                "percent": int(
                    option.css_first(
                        ".tgme_widget_message_poll_option_percent"
                    ).text()[:-1]
                ),
            }
            for option in options
            if option.css_first(".tgme_widget_message_poll_option_text")
            and option.css_first(".tgme_widget_message_poll_option_percent")
        ]

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
        return {
            "question": Post.__extract_poll_question(poll),
            "type": _type.text() if _type else None,
            "votes": buble.css_first(".tgme_widget_message_voters").text(),
            "options": Post.__extract_poll_options(poll),
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
        time = buble.css_first(
            ".tgme_widget_message_date > time"
        ).attributes.get("datetime")
        views = buble.css_first(".tgme_widget_message_views")
        meta = buble.css_first(".tgme_widget_message_meta")
        edited = meta.text().startswith("edited")
        author = cls.author(buble)

        footer = {
            "views": views.text() if views else None,
            "edited": edited,
            "date": {"string": time, "unix": cls.__unix_timestamp(time)},
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
        selector = buble.css_first(
            ".tgme_widget_message_text, .js-message_text"
        )
        if not selector:
            return None

        content = Utils.get_text_html(selector)

        delete_tags = [
            "a",
            "i",
            "b",
            "s",
            "u",
            "pre",
            "code",
            "span",
            "tg-emoji",
            "tg-spoiler",
        ]
        selector.unwrap_tags(delete_tags)
        content_t = Utils.get_text_html(selector)
        text = Post.__clean_html_text(content_t)

        entities = EntitiesParser(content).parse_message()
        response = {"string": text, "html": content}
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
                "url": inline.attributes.get("href"),
            }
            for inline in selector.css(".tgme_widget_message_inline_button")
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
        selector = message.attributes.get("data-post")
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
        forwarded = message.css_first(
            ".tgme_widget_message_forwarded_from_name"
        )
        if not forwarded:
            return None

        url = forwarded.attributes.get("href")
        forwarded = {
            "name": {
                "string": forwarded.text(),
                "html": Utils.get_text_html(forwarded, "span"),
            }
        }
        if url:
            forwarded["url"] = url

        return forwarded

    @classmethod
    def reactions(cls, message: LexborNode) -> Optional[List[Dict[str, Any]]]:
        """Parse reaction information from a message."""
        reactions_node = message.css_first(".tgme_widget_message_reactions")
        if not reactions_node:
            return None

        reactions = []
        for reaction_node in reactions_node.css(".tgme_reaction"):
            reaction = cls.__extract_single_reaction(reaction_node)
            if reaction:
                reactions.append(reaction)

        return reactions or None

    @classmethod
    def __extract_single_reaction(cls, reaction_node: LexborNode) -> Optional[Dict[str, Any]]:
        """Parse a single reaction node into a structured dictionary."""
        reaction = {"count": reaction_node.last_child.text()}

        if "tgme_reaction_paid" in reaction_node.attributes.get("class", ""):
            return cls.__extract_paid_reaction(reaction)
        if reaction_node.css_first("i.emoji"):
            return cls.__extract_emoji_reaction(reaction_node, reaction)
        if reaction_node.css_first("tg-emoji"):
            return cls._parse_custom_emoji_reaction(reaction_node, reaction)

        return None

    @staticmethod
    def __extract_paid_reaction(reaction_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a paid (stars) reaction."""
        reaction_data.update({
            "type": "telegram_stars",
            "emoji": "⭐"
        })
        return reaction_data

    @staticmethod
    def __extract_emoji_reaction(reaction_node: LexborNode, reaction_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a standard emoji reaction."""
        emoji_img = reaction_node.css_first("i.emoji")
        if not emoji_img:
            return None

        emoji_tag = emoji_img.css_first("b")
        if emoji_tag:
            reaction_data["emoji"] = emoji_tag.text()
        else:
            emoji = Post._extract_emoji_from_style(emoji_img)
            if not emoji:
                return None
            reaction_data["emoji"] = emoji

        reaction_data["type"] = "emoji"
        return reaction_data

    @staticmethod
    def _extract_emoji_from_style(emoji_node: LexborNode) -> Optional[str]:
        """Extract emoji from background-image style property."""
        style = emoji_node.attributes.get("style", "")
        if "background-image" not in style:
            return None

        match = re.search(r'/([^/]+)\.png', style)
        if not match:
            return None

        hex_code = match.group(1).upper()
        clean_hex = ''.join(c for c in hex_code if c in '0123456789ABCDEF')

        if len(clean_hex) % 2 != 0:
            clean_hex = clean_hex[:-1]

        try:
            return bytes.fromhex(clean_hex).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _parse_custom_emoji_reaction(reaction_node: LexborNode, reaction_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a custom emoji reaction."""
        emoji_id = reaction_node.css_first("tg-emoji").attributes.get("emoji-id")
        reaction_data.update({
            "type": "custom_emoji",
            "emoji_id": emoji_id
        })
        return reaction_data

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
            "html": Utils.get_text_html(auth, "span"),
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

        cover, name, text = Post.__extract_reply_data(reply)
        if not text:
            return None

        return {
            "cover": (
                Utils.background_extr(cover.attributes.get("style"))
                if cover
                else None
            ),
            "name": {
                "string": name.text().strip(),
                "html": Utils.get_text_html(name, "span"),
            },
            "text": {"string": text.text(), "html": Utils.get_text_html(text)},
            "to_message": int(
                re.search(
                    r"https://t\.me/[\w-]+/(\d+)", reply.attributes.get("href")
                ).group(1)
            ),
        }

    @staticmethod
    def __build_post_object(
        post_instance, message: LexborNode, content: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Builds a complete post object with all metadata.

        Args:
            post_instance: The Post instance.
            message (LexborNode): The message node.
            content (Dict[str, Any]): The parsed content dictionary.

        Returns:
            Dict[str, Any]: A complete post object.
        """
        buble = post_instance.buble(message)
        post = {
            "id": Post.post_id(message),
            "content": content,
            "footer": post_instance.footer(buble),
            "view": message.attributes.get("data-view"),
        }

        forwarded = Post.forwarded(message)
        if forwarded:
            post["forwarded"] = forwarded

        return post

    @staticmethod
    def __check_selector(identifier: int, selector: Optional[int]) -> bool:
        """
        Checks if a message should be processed based on selector.

        Args:
            identifier (int): The message ID.
            selector (Optional[int]): Optional selector to filter messages.

        Returns:
            bool: True if the message should be processed, False otherwise.
        """
        if selector is None:
            return True
        return identifier == selector

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
            if not self.__check_selector(identifier, selector):
                continue

            buble = self.buble(message)
            content = self.__parse_content_fields(self, buble, message)

            posts.append(self.__build_post_object(self, message, content))

        return posts

    def __str__(self) -> str:
        """Return representation for Post object"""
        return json.dumps(self.get())
