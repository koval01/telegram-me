import re
from datetime import datetime
from typing import Literal

from selectolax.lexbor import LexborHTMLParser, LexborNode

from urllib.parse import urlparse, parse_qs
import logging


class Parser:

    def __init__(self, body: str) -> None:
        self.soup = LexborHTMLParser(body)

    @staticmethod
    def _safe_index(array: list, index: int) -> any:
        try:
            return array[index]
        except Exception as e:
            logging.debug(e)
            return

    @classmethod
    def _query(cls, url: str) -> dict:
        parsed_url = urlparse(url)
        dictionary = parse_qs(parsed_url.query)
        updated_dict = [{
            v[0]: cls._set_int(v[1][0])
        } for v in dictionary.items()]
        return updated_dict[0] if updated_dict else {}

    @staticmethod
    def _get_counters(node: list[LexborNode]) -> list[dict]:
        return [
            {
                f.css_first(".counter_type").text(): f.css_first(".counter_value").text()
            }
            for f in node
        ]

    def _get_meta(self, selector: str, name: str) -> str:
        return self._safe_index([
            t.attributes["content"]
            for t in self.soup.css("meta")
            if t.attributes.get(selector) == name
        ], 0)

    @staticmethod
    def _set_int(value: str) -> int | str:
        return int(value) if value.isdigit() else value

    def _get_offset(self, node: LexborNode) -> list[dict]:
        links = node.css("link")
        return [
            self._query(link.attributes.get("href"))
            for link in links
            if link.attributes.get("rel") in ("prev", "next",)
        ]


class Body(Parser):
    def __init__(self, body: str) -> None:
        super().__init__(body)

    async def get(self) -> dict:
        return {
            "channel": {
                "username": self.soup.css_first(".tgme_channel_info_header_username>a").text(),
                "title": self._get_meta("property", "og:title"),
                "description": self._get_meta("property", "og:description"),
                "avatar": self._get_meta("property", "og:image")
            },
            "content": {
                "counters": self._get_counters(
                    self.soup.css(".tgme_channel_info_counters>.tgme_channel_info_counter")),
                "posts": Post(self.soup.body.html).get()
            },
            "meta": {
                "offset": self._get_offset(self.soup.head)
            }
        }


class Post:

    def __init__(self, body: str) -> None:
        self.soup = LexborHTMLParser(body)
        self.buble = lambda m: m.css_first(".tgme_widget_message_bubble")

    @staticmethod
    def __unix_timestamp(timestamp: str) -> int:
        timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
        return int(timestamp.timestamp())

    def __messages(self) -> list[LexborNode]:
        return self.soup.css(".tgme_widget_message_wrap>.tgme_widget_message")

    @staticmethod
    def __duration(duration: str) -> int:
        minutes, seconds = map(int, duration.split(":"))
        total_seconds = minutes * 60 + seconds
        return total_seconds

    @staticmethod
    def __background(style: str) -> str | None:
        match = re.search(
            r"background-image:\s*?url\([',\"](.*)[',\"]\)",
            style, flags=re.I | re.M)
        return match.group(1) \
            if match else None

    @classmethod
    def __media(cls, group: LexborNode) -> list[dict]:
        media = group.css(",".join([
            ".tgme_widget_message_photo_wrap",
            ".tgme_widget_message_video_player",
            ".tgme_widget_message_voice_player",
            ".tgme_widget_message_roundvideo_player"
        ]))
        media_array: list = []

        for m in media:
            content_type: Literal["image", "video", "voice"] = {
                "tgme_widget_message_photo_wrap":         "image",
                "tgme_widget_message_video_player":       "video",
                "tgme_widget_message_voice_player":       "voice",
                "tgme_widget_message_roundvideo_player":  "roundvideo"
            }[m.attributes.get("class").split()[0]]

            match content_type:
                case "image":
                    image = m.attributes.get("style")

                    media_array.append({
                        "url": cls.__background(image),
                        "type": "image"
                    }) if image else None

                case "video":
                    video = m.css_first("video.tgme_widget_message_video")
                    duration = m.css_first("time.message_video_duration").text()

                    media_array.append({
                        "url": video.attributes.get("src"),
                        "thumb": cls.__background(
                            m.css_first(".tgme_widget_message_video_thumb").attributes.get("style")),
                        "duration": {
                            "formatted": duration,
                            "raw": cls.__duration(duration)
                        },
                        "type": "video"
                    }) if video else None

                case "voice":
                    audio = m.css_first(".tgme_widget_message_voice")
                    duration = m.css_first("time.tgme_widget_message_voice_duration").text()

                    media_array.append({
                        "url": audio.attributes.get("src"),
                        "waves": audio.attributes.get("data-waveform"),
                        "duration": {
                            "formatted": duration,
                            "raw": cls.__duration(duration)
                        },
                        "type": "voice"
                    }) if audio else None

                case "roundvideo":
                    roundvideo = m.css_first("video.tgme_widget_message_roundvideo")
                    duration = m.css_first("time.tgme_widget_message_roundvideo_duration").text()

                    media_array.append({
                        "url": roundvideo.attributes.get("src"),
                        "thumb": cls.__background(
                            m.css_first(".tgme_widget_message_roundvideo_thumb").attributes.get("style")),
                        "duration": {
                            "formatted": duration,
                            "raw": cls.__duration(duration)
                        },
                        "type": "roundvideo"
                    }) if roundvideo else None

        return media_array

    @staticmethod
    def __poll(buble: LexborNode) -> dict | None:
        poll = buble.css_first(".tgme_widget_message_poll")
        if not poll:
            return

        _type = poll.css_first(".tgme_widget_message_poll_type")
        options = poll.css(".tgme_widget_message_poll_option")
        return {
            "question": poll.css_first(".tgme_widget_message_poll_question").text(),
            "type": _type.text() if _type else None,
            "votes": buble.css_first(".tgme_widget_message_voters").text(),
            "options": [
                {
                    "name": option.css_first(".tgme_widget_message_poll_option_text").text(),
                    "percent": int(option.css_first(".tgme_widget_message_poll_option_percent").text()[:-1])
                }
                for option in options
            ]
        }

    @classmethod
    def __footer(cls, buble: LexborNode) -> dict:
        time = buble.css_first(".tgme_widget_message_date>time").attributes.get("datetime")
        views = buble.css_first(".tgme_widget_message_views")

        return {
            "views": views.text() if views else None,
            "date": {
                "string": time,
                "unix": cls.__unix_timestamp(time)
            }
        }

    @staticmethod
    def __text(buble: LexborNode) -> dict[str, str] | None:
        selector = buble.css_first(".tgme_widget_message_text")
        if not selector:
            return

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
        selector = message.attributes.get('data-post')
        return int(selector.split("/")[1])

    @staticmethod
    def __forwarded(message: LexborNode) -> dict | None:
        forwarded = message.css_first(".tgme_widget_message_forwarded_from_name")
        if not forwarded:
            return

        url = forwarded.attributes.get("href")
        forwarded = {
            "name": forwarded.text()
        }
        if url:
            forwarded["url"] = url

        return forwarded

    def get(self) -> list[dict] | list:
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
