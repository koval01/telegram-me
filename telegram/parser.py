"""
HTML body parser
"""

import logging
import re
from datetime import datetime
from typing import Literal, Union, List, Any, Dict
from urllib.parse import urlparse, parse_qs

from selectolax.lexbor import LexborHTMLParser, LexborNode


class Misc:
    """
    Misc methods
    """

    @staticmethod
    def safe_index(array: List[Any], index: int) -> Any:
        """
        Safely retrieves an item from a list by index.

        Args:
            array (List[Any]): The list to retrieve the item from.
            index (int): The index of the item to retrieve.

        Returns:
            Any: The item at the specified index, or None if index is out of range.
        """
        try:
            return array[index]
        except IndexError as e:
            logging.debug(e)
            return None

    @staticmethod
    def set_int(value: str) -> Union[int, str]:
        """
        Converts a string to an integer if possible.

        Args:
            value (str): The string value to convert.

        Returns:
            Union[int, str]: The converted integer value or the original string if conversion fails.
        """
        return int(value) if value.isdigit() else value


class Parser:
    """
    A class for parsing HTML content.

    Attributes:
        soup (LexborHTMLParser): An instance of LexborHTMLParser for parsing HTML content.
    """

    def __init__(self, body: str) -> None:
        """
        Initializes the Parser object.

        Args:
            body (str): The HTML content to parse.
        """
        self.soup = LexborHTMLParser(body)

    @staticmethod
    def query(url: str) -> Dict[str, int]:
        """
        Parses query parameters from a URL.

        Args:
            url (str): The URL to parse.

        Returns:
            Dict[str, int]: A dictionary containing parsed query parameters.
        """
        parsed_url = urlparse(url)
        dictionary = parse_qs(parsed_url.query)
        updated_dict = [{
            v[0]: Misc.set_int(v[1][0])
        } for v in dictionary.items()]
        return updated_dict[0] if updated_dict else {}

    @staticmethod
    def get_counters(node: List[LexborNode]) -> Dict[str, str]:
        """
        Extracts counter information from HTML nodes.

        Args:
            node (List[LexborNode]): List of HTML nodes.

        Returns:
            Dict[str, str]: A dictionary containing counter information.
        """
        return {
            f.css_first(".counter_type").text(): f.css_first(".counter_value").text()
            for f in node
        }

    def get_meta(self, selector: str, name: str) -> str:
        """
        Retrieves metadata content from HTML meta tags.

        Args:
            selector (str): The selector attribute to match.
            name (str): The value of the selector attribute.

        Returns:
            str: The content of the meta tag, or an empty string if not found.
        """
        return Misc.safe_index([
            t.attributes["content"]
            for t in self.soup.css("meta")
            if t.attributes.get(selector) == name
        ], 0)

    def get_offset(self, node: LexborNode, more: bool = False) -> dict[str, int]:
        """
        Parses offset values from HTML link tags.

        Args:
            node (LexborNode): The HTML node to search for link tags.
            more (bool): Is more response

        Returns:
            dict[str, int]: A dictionary containing parsed offset values.
        """
        links = node.css("a.tme_messages_more" if more else "link")
        keys = {}

        for link in links:
            body: bool = link.attributes.get("rel") in ("prev", "next",)
            more: bool = any(
                key in link.attributes.keys()
                for key in ("data-before", "data-after",))
            if body or more:
                for k, v in self.query(link.attributes.get("href")).items():
                    keys[k] = v

        return keys


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

    def get(self) -> dict[
            str, dict[str, str]
            | dict[str, list[dict[str, str]] | list[dict] | list]
            | dict[str, list[dict[str, int]]]
    ]:
        """
        Extracts relevant information from the HTML body.

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
                "posts": Post(self.soup.body.html).get()
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
        return repr(self.get())


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
        return repr(self.get())


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

    def __messages(self) -> List[LexborNode]:
        """
        Retrieves message nodes from the HTML content.

        Returns:
            List[LexborNode]: A list of message nodes.
        """
        return self.soup.css(".tgme_widget_message_wrap > .tgme_widget_message")

    @staticmethod
    def __duration(duration: str) -> int | None:
        """
        Converts a duration string (MM:SS) to total seconds.

        Args:
            duration (str): The duration string in the format "MM:SS".

        Returns:
            int: The total duration in seconds.
        """
        if not duration:
            return None
        minutes, seconds = map(int, duration.split(":"))
        return minutes * 60 + seconds

    @staticmethod
    def __background(style: str) -> Union[str, None]:
        """
        Extracts the background image URL from a CSS style string.

        Args:
            style (str): The CSS style string.

        Returns:
            Union[str, None]: The background image URL, or None if not found.
        """
        match = re.search(
            r"background-image:\s*?url\([',\"](.*)[',\"]\)",
            style, flags=re.I | re.M)
        return match.group(1) if match else None

    @classmethod
    def __media(cls, group: LexborNode) -> List[dict]:
        """
        Extracts media content from a message.

        Args:
            group (LexborNode): The message group node.

        Returns:
            List[dict]: A list of dictionaries containing media information.
        """
        media = group.css(",".join([
            ".tgme_widget_message_photo_wrap",
            ".tgme_widget_message_video_player",
            ".tgme_widget_message_voice_player",
            ".tgme_widget_message_roundvideo_player"
        ]))
        media_array = []

        for m in media:
            content_type: Literal["image", "video", "voice"] = {
                "tgme_widget_message_photo_wrap": "image",
                "tgme_widget_message_video_player": "video",
                "tgme_widget_message_voice_player": "voice",
                "tgme_widget_message_roundvideo_player": "roundvideo"
            }[m.attributes.get("class").split()[0]]

            match content_type:
                case "image":
                    image = m.attributes.get("style")
                    if image:
                        media_array.append({
                            "url": cls.__background(image),
                            "type": "image"
                        })

                case "video":
                    video = m.css_first("video.tgme_widget_message_video")
                    thumb = m.css_first(".tgme_widget_message_video_thumb")
                    duration = m.css_first("time.message_video_duration")
                    duration = duration.text() if duration else None
                    if video:
                        media_array.append({
                            "url": video.attributes.get("src"),
                            "thumb": cls.__background(
                                thumb.attributes.get("style")
                            ) if thumb else None,
                            "duration": {
                                "formatted": duration,
                                "raw": cls.__duration(duration)
                            },
                            "type": "video"
                        })

                case "voice":
                    audio = m.css_first(".tgme_widget_message_voice")
                    duration = m.css_first("time.tgme_widget_message_voice_duration").text()
                    if audio:
                        media_array.append({
                            "url": audio.attributes.get("src"),
                            "waves": audio.attributes.get("data-waveform"),
                            "duration": {
                                "formatted": duration,
                                "raw": cls.__duration(duration)
                            },
                            "type": "voice"
                        })

                case "roundvideo":
                    roundvideo = m.css_first("video.tgme_widget_message_roundvideo")
                    duration = m.css_first("time.tgme_widget_message_roundvideo_duration").text()
                    if roundvideo:
                        media_array.append({
                            "url": roundvideo.attributes.get("src"),
                            "thumb": cls.__background(
                                m.css_first(".tgme_widget_message_roundvideo_thumb")
                                .attributes.get("style")),
                            "duration": {
                                "formatted": duration,
                                "raw": cls.__duration(duration)
                            },
                            "type": "roundvideo"
                        })

        return media_array

    @staticmethod
    def __poll(buble: LexborNode) -> Union[dict, None]:
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
    def __footer(cls, buble: LexborNode) -> dict:
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

        return {
            "views": views.text() if views else None,
            "date": {
                "string": time,
                "unix": cls.__unix_timestamp(time)
            }
        }

    @staticmethod
    def __text(buble: LexborNode) -> Union[dict, None]:
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

        t_selector = buble.css_first(".tgme_widget_message_text")
        delete_tags = ["a", "i", "b", "pre", "code", "tg-emoji", "span"]
        t_selector.unwrap_tags(delete_tags)
        html = t_selector.html
        content = re.match(r"<div.*?>(.*)</div>", html, flags=re.M).group(1)
        text = re.sub(r"<br\s?/?>", "\n", content)

        return {
            "string": text,
            "entities": []
        }

    @staticmethod
    def __post_id(message: LexborNode) -> int:
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
    def __forwarded(message: LexborNode) -> Union[dict, None]:
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

    def get(self) -> List[dict]:
        """
        Extracts post information from the HTML content.

        Returns:
            List[dict]: A list of dictionaries containing post information.
        """
        posts = []
        for message in self.__messages():
            buble = self.buble(message)

            content = {}
            text = self.__text(buble)
            if text:
                content["text"] = text
            media = self.__media(buble)
            if media:
                content["media"] = media
            poll = self.__poll(buble)
            if poll:
                content["poll"] = poll

            post = {
                "id": self.__post_id(message),
                "content": content,
                "footer": self.__footer(buble)
            }
            forwarded = self.__forwarded(message)
            if forwarded:
                post["forwarded"] = forwarded

            posts.append(post)

        return posts

    def __str__(self) -> str:
        """
        Return representation for Post object
        """
        return repr(self.get())
