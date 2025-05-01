import json
from typing import Optional, Dict, List, Tuple, Any

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

    Private Methods:
        _get_media_attributes(match: LexborNode, selectors: Dict) -> Dict:
            Helper method to extract common attributes from media elements.
        _parse_duration(duration_node: Optional[LexborNode]) -> Dict:
            Extracts duration information from a duration node.
        _extract_thumbnail(node: LexborNode, selector: str) -> Optional[str]:
            Extracts thumbnail URL from a node using the specified selector.
        _get_content_type(class_name: str) -> str:
            Maps class name to media content type.
        _build_media_data(data_dict: Dict) -> Dict:
            Builds a standardized media data dictionary.
        _get_media_selectors() -> str:
            Returns selector string for finding media elements.
    """

    _MEDIA_CLASSES = [
        ".link_preview_image",
        ".tgme_widget_message_photo_wrap",
        ".tgme_widget_message_video_player",
        ".tgme_widget_message_voice_player",
        ".tgme_widget_message_roundvideo_player",
        ".tgme_widget_message_sticker_wrap",
    ]

    _CONTENT_TYPE_MAP = {
        "link_preview_image": "image",
        "tgme_widget_message_photo_wrap": "image",
        "tgme_widget_message_video_player": "video",
        "tgme_widget_message_voice_player": "voice",
        "tgme_widget_message_roundvideo_player": "roundvideo",
        "tgme_widget_message_sticker_wrap": "sticker",
    }

    def __init__(self, group: LexborNode) -> None:
        """
        Initializes a Media object with the provided HTML group.

        Args:
            group (LexborNode): The HTML group containing media elements.
        """
        self.media = group.css(self._get_media_selectors())

    @classmethod
    def image(cls, match: LexborNode) -> Optional[Dict[str, str]]:
        """
        Extracts information about an image media element.

        Args:
            match (LexborNode): The HTML node representing the image media element.

        Returns:
            Optional[Dict[str, str]]: A dictionary containing information
            about the image, or None if no image found.
        """
        image: Optional[str] = match.attributes.get("style")

        if not image:
            return None

        return cls._build_media_data(
            {"url": Utils.background_extr(image), "type": "image"}
        )

    @classmethod
    def video(cls, match: LexborNode) -> Optional[Dict[str, Any]]:
        """
        Extracts information about a video media element, handling various cases:
        - Normal available videos
        - Too big videos (only placeholder available)
        - GIFs (videos without duration)

        Args:
            match (LexborNode): The HTML node representing the video media element.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing information
            about the video, or None if no video found.
        """
        selectors = {
            "thumb": ".tgme_widget_message_video_thumb",
            "duration": "time.message_video_duration",
            "video": "video.tgme_widget_message_video",
        }
        attrs = cls._get_media_attributes(match, selectors)

        video_available = bool(attrs["video"] and attrs["video"].attributes.get("src"))

        body = {
            "url": attrs["video"].attributes.get("src") if video_available else None,
            "thumb": (
                Utils.background_extr(attrs["thumb"].attributes.get("style"))
                if attrs["thumb"]
                else None
            ),
            "type": "video",
        }
        if not video_available:
            body["available"] = video_available

        if attrs["duration"]:
            duration_text = attrs["duration"].text()
            body["duration"] = cls._parse_duration(duration_text)
        else:
            body["type"] = "gif"

        return cls._build_media_data(body)

    @classmethod
    def voice(cls, match: LexborNode) -> Optional[Dict[str, Any]]:
        """
        Extracts information about a voice media element.

        Args:
            match (LexborNode): The HTML node representing the voice media element.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing information
            about the voice media, or None if no voice media found.
        """
        audio: Optional[LexborNode] = match.css_first(
            ".tgme_widget_message_voice"
        )
        duration_node: Optional[LexborNode] = match.css_first(
            "time.tgme_widget_message_voice_duration"
        )

        if not audio:
            return None

        duration_text = duration_node.text() if duration_node else None

        return cls._build_media_data(
            {
                "url": audio.attributes.get("src"),
                "waves": audio.attributes.get("data-waveform"),
                "duration": cls._parse_duration(duration_text),
                "type": "voice",
            }
        )

    @classmethod
    def roundvideo(cls, match: LexborNode) -> Optional[Dict[str, Any]]:
        """
        Extracts information about a round video media element.

        Args:
            match (LexborNode): The HTML node representing the round video media element.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing information about
            the round video media, or None if no round video found.
        """
        roundvideo: Optional[LexborNode] = match.css_first(
            "video.tgme_widget_message_roundvideo"
        )
        duration_node: Optional[LexborNode] = match.css_first(
            "time.tgme_widget_message_roundvideo_duration"
        )

        if not roundvideo:
            return None

        thumbnail = cls._extract_thumbnail(
            match, ".tgme_widget_message_roundvideo_thumb"
        )
        duration_text = duration_node.text() if duration_node else None

        return cls._build_media_data(
            {
                "url": roundvideo.attributes.get("src"),
                "thumb": thumbnail,
                "duration": cls._parse_duration(duration_text),
                "type": "roundvideo",
            }
        )

    @classmethod
    def sticker(cls, match: LexborNode) -> Optional[Dict[str, Any]]:
        """
        Extracts information about a sticker media element.

        Args:
            match (LexborNode): The HTML node representing the sticker media element.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing information about
            the sticker media, or None if no sticker is found.
        """
        sticker_classes = cls._get_sticker_classes()
        key_map = cls._get_sticker_key_map()

        sticker, key_idx = cls._find_sticker_element(match, sticker_classes)

        if not sticker or key_idx is None:
            return None

        key = key_map[key_idx]
        source = sticker.css_first(key[0])

        if not source:
            return None

        thumb = source.css_first("img")

        body = {"url": source.attributes.get(key[1]), "type": "sticker"}

        if thumb:
            body["thumb"] = thumb.attributes.get("src")

        return cls._build_media_data(body)

    def extract_media(self) -> List[Dict]:
        """
        Extracts and returns information about all media elements in the group.

        Returns:
            List[Dict]: A list containing dictionaries, each
            representing information about a media element.
        """
        media_array: List[Dict] = []

        for media_element in self.media:
            media_data = self._process_media_element(media_element)
            if media_data:
                media_array.append(media_data)

        return media_array

    def _process_media_element(self, element: LexborNode) -> Optional[Dict]:
        """
        Process a single media element and extract its data.

        Args:
            element (LexborNode): The media element to process

        Returns:
            Optional[Dict]: The extracted media data or None if processing failed
        """
        class_name = element.attributes.get("class", "").split()[0]
        content_type = self._get_content_type(class_name)

        if not content_type:
            return None

        processor_map = {
            "image": self.image,
            "video": self.video,
            "voice": self.voice,
            "roundvideo": self.roundvideo,
            "sticker": self.sticker,
        }

        processor = processor_map.get(content_type)
        if not processor:
            return None

        return processor(element)

    @staticmethod
    def __duration(duration: str) -> Optional[int]:
        """
        Converts a duration string (HH:MM:SS or MM:SS) to total seconds.

        Args:
            duration (str): The duration string in either "HH:MM:SS" or "MM:SS" format.

        Returns:
            Optional[int]: The total duration in seconds, or None if the input is invalid.
        """
        if not duration:
            return None

        try:
            parts = list(map(int, duration.split(":")))

            if len(parts) == 2:
                # MM:SS format
                minutes, seconds = parts
                return minutes * 60 + seconds
            if len(parts) == 3:
                # HH:MM:SS format
                hours, minutes, seconds = parts
                return hours * 3600 + minutes * 60 + seconds

            return None
        except (ValueError, TypeError) as _:
            return None

    def __str__(self) -> str:
        """
        Returns a JSON representation of the extracted media elements.

        Returns:
            str: A JSON string representing the extracted media elements.
        """
        return json.dumps(self.extract_media())

    @classmethod
    def _get_media_attributes(
        cls, match: LexborNode, selectors: Dict[str, str]
    ) -> Dict[str, Optional[LexborNode]]:
        """
        Helper method to extract common attributes from media elements.

        Args:
            match (LexborNode): The HTML node to extract attributes from
            selectors (Dict[str, str]): Dictionary mapping attribute names to CSS selectors

        Returns:
            Dict[str, Optional[LexborNode]]: Dictionary of extracted nodes
        """
        result = {}
        for attr_name, selector in selectors.items():
            result[attr_name] = match.css_first(selector)
        return result

    @classmethod
    def _parse_duration(
        cls, duration_text: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Parses a duration text into a structured dictionary.

        Args:
            duration_text (Optional[str]): The duration text in MM:SS format

        Returns:
            Optional[Dict[str, Any]]: Dictionary with formatted and raw duration values
        """
        if not duration_text:
            return None

        return {
            "formatted": duration_text,
            "raw": cls.__duration(duration_text),
        }

    @classmethod
    def _extract_thumbnail(
        cls, node: LexborNode, selector: str
    ) -> Optional[str]:
        """
        Extracts a thumbnail URL from the given node using the specified selector.

        Args:
            node (LexborNode): The node containing the thumbnail
            selector (str): CSS selector for the thumbnail element

        Returns:
            Optional[str]: The extracted thumbnail URL or None if not found
        """
        thumb_node = node.css_first(selector)
        if not thumb_node:
            return None

        style = thumb_node.attributes.get("style")
        if not style:
            return None

        return Utils.background_extr(style)

    @classmethod
    def _get_content_type(cls, class_name: str) -> Optional[str]:
        """
        Maps a class name to a media content type.

        Args:
            class_name (str): The CSS class name

        Returns:
            Optional[str]: The corresponding content type or None if not found
        """
        return cls._CONTENT_TYPE_MAP.get(class_name)

    @classmethod
    def _build_media_data(cls, data_dict: Dict) -> Dict:
        """
        Builds a standardized media data dictionary, ensuring all required fields are present.

        Args:
            data_dict (Dict): The raw media data

        Returns:
            Dict: Standardized media data dictionary
        """
        # Ensure all required fields are present
        if "url" not in data_dict or "type" not in data_dict:
            raise ValueError("Media data must contain 'url' and 'type' fields")

        return data_dict

    @classmethod
    def _get_media_selectors(cls) -> str:
        """
        Returns a comma-joined string of media element selectors.

        Returns:
            str: Comma-separated CSS selectors for media elements
        """
        return ",".join(cls._MEDIA_CLASSES)

    @classmethod
    def _get_sticker_classes(cls) -> Tuple[str, ...]:
        """
        Returns the tuple of sticker class selectors.

        Returns:
            Tuple[str, ...]: Tuple of CSS selectors for sticker elements
        """
        return (
            "picture.tgme_widget_message_tgsticker",
            "i.tgme_widget_message_sticker",
            "div.tgme_widget_message_videosticker",
        )

    @classmethod
    def _get_sticker_key_map(cls) -> Tuple[Tuple[str, str], ...]:
        """
        Returns the mapping of sticker element types to their attribute names.

        Returns:
            Tuple[Tuple[str, str], ...]: Tuple of (selector, attribute) pairs
        """
        return (
            (
                "source",
                "srcset",
            ),
            (
                "i.tgme_widget_message_sticker",
                "data-webp",
            ),
            (
                "video.js-videosticker_video",
                "src",
            ),
        )

    @classmethod
    def _find_sticker_element(
        cls, match: LexborNode, sticker_classes: Tuple[str, ...]
    ) -> Tuple[Optional[LexborNode], Optional[int]]:
        """
        Finds a sticker element within the given node.

        Args:
            match (LexborNode): The node to search within
            sticker_classes (Tuple[str, ...]): Tuple of CSS selectors to try

        Returns:
            Tuple[Optional[LexborNode], Optional[int]]: The found sticker element and its index,
                                                        or (None, None) if not found
        """
        for i, class_selector in enumerate(sticker_classes):
            sticker = match.css_first(class_selector)
            if sticker:
                return sticker, i

        return None, None
