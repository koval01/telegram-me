"""
Utility module providing HTML parsing and text
extraction functionality using the selectolax library.
This module contains helper methods for
processing HTML content in a structured and reusable way.
"""

import re
from typing import Union, Optional

from selectolax.lexbor import LexborNode


class Utils:
    """
    A utility class providing static methods for HTML content manipulation and text extraction.

    This class serves as a collection of helper methods that can be used across the application
    for common HTML processing tasks. It primarily focuses on extracting and processing text
    content from HTML elements using regular expressions and the selectolax library.
    """

    @staticmethod
    def get_text_html(
        selector: LexborNode, tag_name: str = "div"
    ) -> Optional[str]:
        """
        Extracts and returns the inner HTML content of the first element with the specified tag
        name found in the HTML represented by the LexborNode object.

        Args:
            selector (LexborNode): A LexborNode object containing the HTML content to be searched.
            tag_name (str): The name of the tag to search for (e.g., 'div', 'span').

        Returns:
            Optional[str]: The inner HTML content of the first matching element,
                if found; otherwise, `None`.
        """
        # Escape the tag name to avoid special regex characters
        escaped_tag_name = re.escape(tag_name)
        # Construct regex to match the tag
        pattern = rf"<{escaped_tag_name}.*?>(.*?)</{escaped_tag_name}>"
        match = re.search(
            pattern, selector.html, flags=re.M | re.S
        )  # Allow multiline and dot-all
        if match:
            return match.group(1)

        return None

    @staticmethod
    def strip_html_tags(html_content: str) -> str:
        """
        Removes all HTML tags from the given string while preserving the text content.

        Args:
            html_content (str): The HTML content to be stripped.

        Returns:
            str: The text content with all HTML tags removed.
        """
        return re.sub(r"<[^>]+>", "", html_content)

    @staticmethod
    def background_extr(style: str) -> Union[str, None]:
        """
        Extracts the background image URL from a CSS style string.

        Args:
            style (str): The CSS style string.

        Returns:
            Union[str, None]: The background image URL, or None if not found.
        """
        match = re.search(
            r"background-image:\s*?url\([',\"](.*)[',\"]\)",
            style,
            flags=re.I | re.M,
        )
        return match.group(1) if match else None
