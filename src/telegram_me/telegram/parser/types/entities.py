import re
import json

from typing import AnyStr, Dict, List, Optional, Tuple, Union, Any


class EntitiesParser:
    """
    A parser for extracting various entities from HTML text, including hashtags,
    formatting tags (bold, italic, etc.), code snippets, strikethrough text,
    spoilers, and links. The parser also supports extracting plain text from HTML
    and identifies the type, offset, and length of each entity.

    Attributes:
        patterns (tuple): A tuple of regex patterns for matching HTML entities.
        idx_map (tuple): A tuple mapping group indices in regex matches to entity types.
        html_text (str): The HTML input text with line breaks normalized.
        text_only (str): The plain text extracted from `html_text`,
            with all HTML tags removed.

    Args:
        html_body (str): The HTML content to be parsed.
    """

    def __init__(self, html_body: str) -> None:
        """Initialize the parser with HTML content."""
        self.patterns: tuple = (
            r"(#\w+)",  # hashtag
            r"<b>(.+?)<\/b>(?![^<]*<\/i>)",  # bold
            r"<i>(.+?)<\/i>",  # italic
            r"<u>(.+?)<\/u>",  # underline
            r"<code>(.+?)<\/code>",  # code
            r"<s>(.+?)<\/s>",  # strikethrough
            r"<tg-spoiler>(.+?)<\/tg-spoiler>",  # spoiler
            # emoji
            r'<i\s+class="emoji"\s+style=".*?:url\(\'(.*?)\'\)">'
            r"<b>(.*?)<\/b><\/i>(?![^<]*<\/tg-emoji>)",
            # link in text with onclick
            r"<a\s+(?:[^>]*?\s+)?href=\"([^\"]*)\"[^>]*\s+onclick=\"[^\"]*\"[^>]*>(.*?)<\/a>",
            r"<a\s+(?:[^>]*?\s+)?href=\"(?!(?:.*#))(.*?)\"[^>]*>(.*?)<\/a>",  # url
            # animoji
            r'<tg-emoji.*?><i\s+class="emoji"\s+style="background-image:url\(\'(.*?)\'\)">'
            r"<b>(.*?)</b></i></tg-emoji>",
        )
        self.idx_map: tuple = (1, 3, 5, 7, 9, 11, 13, 14, 17, 20, 23)

        # Map entity types to their depth offsets
        self.entity_depth_map: Dict[str, int] = {
            "text_link": 0,
            "emoji": 1,
            "animoji": 2,
        }

        # Map indices to entity types
        self.entity_types: Tuple[Optional[str], ...] = (
            "hashtag",
            "bold",
            "italic",
            "underline",
            "code",
            "strikethrough",
            "spoiler",
            "emoji",
            "text_link",
            None,
            "url",
            "animoji",
        )

        self.html_text: str = re.sub(r"<br\s?/?>", "\n", html_body)
        self.text_only: str = re.sub(r"<[^>]+>", "", self.html_text)

    @property
    def combined_pattern(self) -> re.Pattern[AnyStr]:
        """
        Compiles and returns a single regex pattern that combines all entity patterns.

        Returns:
            re.Pattern[AnyStr]: The compiled regex pattern for matching all entities.
        """
        return re.compile(
            "|".join([f"({p})" for p in self.patterns]), flags=re.DOTALL | re.M
        )

    @staticmethod
    def extract_content(match: re.Match[str], depth: int = 1) -> str:
        """
        Extracts the content of a matched entity from within HTML tags.

        Args:
            match (re.Match[str]): A match object containing the entity.
            depth (int): Tag immersion depth.

        Returns:
            str: The extracted content of the matched entity.
        """
        return match.group().strip("<>").split(">")[-abs(depth)].split("<")[0]

    def length(self, match: re.Match[str], depth: int = 1) -> int:
        """
        Calculates the length of the matched entity's text content.

        Args:
            match (re.Match[str]): A match object containing the entity.
            depth (int): Tag immersion depth

        Returns:
            int: The length of the entity's text content.
        """
        return len(self.extract_content(match, depth))

    def offset(
        self, text_start: int, match: re.Match[str], depth: int = 1
    ) -> int:
        """
        Calculates the end offset of the matched entity in the plain text.

        Args:
            text_start (int): The starting index of the entity in the plain text.
            match (re.Match[str]): A match object containing the entity.
            depth (int): Tag immersion depth

        Returns:
            int: The end offset of the entity in the plain text.
        """
        return text_start + len(self.extract_content(match, depth))

    def text_start(
        self, text: str, match: re.Match[str], offset: int, depth: int = 1
    ) -> int:
        """
        Finds the starting index of the matched entity's text content in the plain text.

        Args:
            text (str): The plain text from which entities are extracted.
            match (re.Match[str]): A match object containing the entity.
            offset (int): The current offset in the plain text to start the search from.
            depth (int): Tag immersion depth

        Returns:
            int: The starting index of the entity in the plain text.
        """
        return text.find(self.extract_content(match, depth), offset)

    def message_type(self, idx: int) -> Optional[str]:
        """
        Determines the entity type based on the index of a matched group.

        Args:
            idx (int): The index of the matched group in the regex pattern.

        Returns:
            str | None: The entity type as a string or Nothing.
        """
        try:
            return self.entity_types[idx // 2]
        except IndexError:
            return None

    def _identify_entity_type(
        self, match: re.Match[str]
    ) -> Tuple[Optional[str], Optional[str], int]:
        """
        Identifies the entity type, URL, and depth from a regex match.

        Args:
            match (re.Match[str]): A match object containing the entity.

        Returns:
            Tuple[Optional[str], Optional[str], int]: A tuple containing the entity type,
                                                     URL (if applicable), and depth.
        """
        entity_type = None
        entity_url = None
        depth = 1

        # Find matching group and entity type
        for idx, group in enumerate(match.groups()):
            if group and idx in self.idx_map:
                entity_type = self.message_type(idx)
                if entity_type in self.entity_depth_map:
                    entity_url = match.group(idx + 2)
                    depth += self.entity_depth_map[entity_type]
                break

        return entity_type, entity_url, depth

    def _calculate_positions(
        self, match: re.Match[str], offset: int, depth: int
    ) -> Tuple[int, int]:
        """
        Calculates the start position and new offset for an entity in the text.

        Args:
            match (re.Match[str]): A match object containing the entity.
            offset (int): The current offset in the plain text.
            depth (int): Tag immersion depth.

        Returns:
            Tuple[int, int]: The start position and new offset.
        """
        # Find start position in text only
        text_start = self.text_start(self.text_only, match, offset, depth)

        # Update offset to the end of the current match in the text only
        new_offset = self.offset(text_start, match, depth)

        return text_start, new_offset

    def _create_entity_dict(
        self,
        entity_info: Dict[str, Any],
        match: re.Match[str],
    ) -> Dict[str, Union[int, str]]:
        """
        Creates a dictionary representation of an entity.

        Args:
            entity_info: Dictionary containing:
                - type: The type of the entity
                - text_start: The starting index in plain text
                - url: Optional URL associated with the entity
                - depth: Tag immersion depth
            match: A match object containing the entity

        Returns:
            Dictionary representing the entity
        """
        entity = {
            "offset": entity_info["text_start"],
            "length": self.length(match, entity_info.get("depth", 1)),
            "type": entity_info["type"],
        }

        if "url" in entity_info:
            url = entity_info["url"]
            if entity_info["type"] in ("emoji", "animoji"):
                entity["url"] = f"https:{url}"
            else:
                entity["url"] = url

        return entity

    def _process_entity_match(
        self, match: re.Match[str], offset: int
    ) -> Tuple[Optional[Dict], int]:
        """
        Process a single regex match and create an entity dict if valid.

        Args:
            match (re.Match[str]): A match object containing the entity.
            offset (int): The current offset in the plain text.

        Returns:
            Tuple[Optional[Dict], int]: A tuple containing the entity dict (or None if invalid)
                                        and the updated offset.
        """
        # Identify entity type, URL, and depth
        entity_type, entity_url, depth = self._identify_entity_type(match)

        if not entity_type:
            return None, offset

        # Calculate positions
        text_start, new_offset = self._calculate_positions(
            match, offset, depth
        )

        # Create entity dictionary
        entity_info = {
            "type": entity_type,
            "text_start": text_start,
            "depth": depth,
        }

        if entity_url:
            entity_info["url"] = entity_url

        entity = self._create_entity_dict(entity_info, match)

        return entity, new_offset

    def parse_message(self) -> List[Dict[str, Union[int, str, None]]]:
        """
        Parses the HTML text and extracts entities, returning a list of entities with details
        about their type, offset, and length. Links include an additional URL attribute.

        Returns:
            List[Dict[str, int | str | None]]: A list of dictionaries,
                each representing an entity with its
                type, offset, length, and optionally, URL.
        """
        entities = []
        offset = 0

        matches = list(re.finditer(self.combined_pattern, self.html_text))
        for match in matches:
            entity, new_offset = self._process_entity_match(match, offset)
            if entity:
                entities.append(entity)
                offset = new_offset

        return entities

    def __str__(self) -> str:
        """
        Returns a string representation of the parsed entities in JSON format.

        Returns:
            str: The JSON string representation of the parsed entities.
        """
        return json.dumps(self.parse_message())
