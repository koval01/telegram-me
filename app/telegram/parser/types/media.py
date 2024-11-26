"""Media module"""

import json

from typing import Literal

from selectolax.lexbor import LexborNode
from app.telegram.parser.methods.utils import Utils


class Media:
    """
    Represents a collection of media elements extracted from an HTML group.

    Args:
        group (LexborNode):
            The HTML group containing media elements.

    Attributes:
        media (List[LexborNode]):
            List of LexborNode objects representing media elements.

    Methods:
        image(match: LexborNode) -> Optional[Dict[str, str]]:
            Extracts information about an image media element.
        video(match: LexborNode) -> Optional[Dict[str, str]]:
            Extracts information about a video media element.
        voice(match: LexborNode) -> Optional[Dict[str, str]]:
            Extracts information about a voice media element.
        roundvideo(match: LexborNode) -> Optional[Dict[str, str]]:
            Extracts information about a round video media element.
        sticker(match: LexborNode) -> Optional[Dict[str, str]]:
            Extracts information about a sticker media element.
        extract_media() -> List[Dict]:
            Extracts and returns information about all media elements in the group.

    Static Methods:
        __duration(duration: str) -> Optional[int]:
            Converts a duration string (MM:SS) to total seconds.

    """

    def __init__(self, group: LexborNode) -> None:
        """
        Initializes a Media object with the provided HTML group.

        Args:
            group (LexborNode): The HTML group containing media elements.
        """
        self.media = group.css(",".join([
            ".tgme_widget_message_photo_wrap",
            ".tgme_widget_message_video_player",
            ".tgme_widget_message_voice_player",
            ".tgme_widget_message_roundvideo_player",
            ".tgme_widget_message_sticker_wrap"
        ]))

    @classmethod
    def image(cls, match: LexborNode) -> dict[str, str] | None:
        """
        Extracts information about an image media element.

        Args:
            match (LexborNode): The HTML node representing the image media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information
            about the image, or None if no image found.
        """
        image: str | None = match.attributes.get("style")

        if not image:
            return None

        return {
            "url": Utils.background_extr(image),
            "type": "image"
        }

    @classmethod
    def video(cls, match: LexborNode) -> dict[str, str] | None:
        """
        Extracts information about a video media element.

        Args:
            match (LexborNode): The HTML node representing the video media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information
            about the video, or None if no video found.
        """
        video: LexborNode | None = match.css_first(
            "video.tgme_widget_message_video")
        thumb: LexborNode | None = match.css_first(
            ".tgme_widget_message_video_thumb")
        duration: LexborNode | None = match.css_first(
            "time.message_video_duration")
        duration: str | None = duration.text() if duration else None

        if not video:
            return None

        body = {
            "url": video.attributes.get("src"),
            "thumb": Utils.background_extr(
                thumb.attributes.get("style")
            ) if thumb else None,
            "type": "video"
        }
        if duration:
            body["duration"] = {
                "formatted": duration,
                "raw": cls.__duration(duration)
            }
        else:
            body["type"] = "gif"

        return body

    @classmethod
    def voice(cls, match: LexborNode) -> dict[str, str] | None:
        """
        Extracts information about a voice media element.

        Args:
            match (LexborNode): The HTML node representing the voice media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information
            about the voice media, or None if no voice media found.
        """
        audio: LexborNode | None = match.css_first(
            ".tgme_widget_message_voice")
        duration: str | None = match.css_first(
            "time.tgme_widget_message_voice_duration").text()

        if not audio:
            return None

        return {
            "url": audio.attributes.get("src"),
            "waves": audio.attributes.get("data-waveform"),
            "duration": {
                "formatted": duration,
                "raw": cls.__duration(duration)
            },
            "type": "voice"
        }

    @classmethod
    def roundvideo(cls, match: LexborNode) -> dict[str, str] | None:
        """
        Extracts information about a round video media element.

        Args:
            match (LexborNode): The HTML node representing the round video media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information about
            the round video media, or None if no round video found.
        """
        roundvideo: LexborNode | None = match.css_first(
            "video.tgme_widget_message_roundvideo")
        duration: str | None = match.css_first(
            "time.tgme_widget_message_roundvideo_duration").text()

        if not roundvideo:
            return None

        return {
            "url": roundvideo.attributes.get("src"),
            "thumb": Utils.background_extr(
                match.css_first(".tgme_widget_message_roundvideo_thumb")
                .attributes.get("style")),
            "duration": {
                "formatted": duration,
                "raw": cls.__duration(duration)
            },
            "type": "roundvideo"
        }

    @classmethod
    def sticker(cls, match: LexborNode) -> dict[str, str] | None:
        """
        Extracts information about a sticker media element.

        Args:
            match (LexborNode): The HTML node representing the sticker media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information about
            the sticker media, or None if no sticker is found.

        The method checks for the presence of a sticker element within the provided
        HTML node, iterating over a predefined set of class selectors. If a sticker
        element is found, it extracts the relevant attributes to construct a dictionary
        containing the sticker's URL and type. If a thumbnail image is associated with
        the sticker, its URL is also included in the dictionary.
        """
        classes: tuple = (
            "picture.tgme_widget_message_tgsticker",
            "i.tgme_widget_message_sticker",
            "div.tgme_widget_message_videosticker",)

        key: tuple[tuple[str, ...], ...] | str = (
            ("source", "srcset",),
            ("i.tgme_widget_message_sticker", "data-webp",),
            ("video.js-videosticker_video", "src",),)
        sticker: LexborNode | None = None

        for i, cl in enumerate(classes):
            sticker = match.css_first(cl)
            if sticker:
                key = key[i]
                break

        if not sticker:
            return None

        source: LexborNode | None = sticker.css_first(key[0])

        if not source:
            return None

        thumb: LexborNode | None = source.css_first("img")

        body = {
            "url": source.attributes.get(key[1]),
            "type": "sticker"
        }
        if thumb:
            body["thumb"] = thumb.attributes.get("src")

        return body

    def extract_media(self) -> list[dict]:
        """
        Extracts and returns information about all media elements in the group.

        Returns:
            List[Dict]: A list containing dictionaries, each
            representing information about a media element.
        """
        media_array: list = []

        for m in self.media:
            content_type: Literal["image", "video", "voice", "roundvideo", "sticker"] = {
                "tgme_widget_message_photo_wrap": "image",
                "tgme_widget_message_video_player": "video",
                "tgme_widget_message_voice_player": "voice",
                "tgme_widget_message_roundvideo_player": "roundvideo",
                "tgme_widget_message_sticker_wrap": "sticker"
            }[m.attributes.get("class").split()[0]]

            match content_type:
                case "image":
                    media_array.append(self.image(m))

                case "video":
                    media_array.append(self.video(m))

                case "voice":
                    media_array.append(self.voice(m))

                case "roundvideo":
                    media_array.append(self.roundvideo(m))

                case "sticker":
                    media_array.append(self.sticker(m))

        media_array: list = [m for m in media_array if m]
        return media_array

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

        minutes, seconds \
            = map(int, duration.split(":"))
        return minutes * 60 + seconds

    def __str__(self) -> str:
        """
        Returns a JSON representation of the extracted media elements.

        Returns:
            str: A JSON string representing the extracted media elements.
        """
        return json.dumps(self.extract_media())
