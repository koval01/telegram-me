"""
Utility module providing HTML parsing and text
extraction functionality using the selectolax library.
This module contains helper methods for
processing HTML content in a structured and reusable way.
"""

import re
from selectolax.lexbor import LexborNode


class Utils:
    """
    A utility class providing static methods for HTML content manipulation and text extraction.

    This class serves as a collection of helper methods that can be used across the application
    for common HTML processing tasks. It primarily focuses on extracting and processing text
    content from HTML elements using regular expressions and the selectolax library.
    """

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

    @staticmethod
    def strip_html_tags(html_content: str) -> str:
        """
        Removes all HTML tags from the given string while preserving the text content.

        Args:
            html_content (str): The HTML content to be stripped.

        Returns:
            str: The text content with all HTML tags removed.
        """
        return re.sub(r'<[^>]+>', '', html_content)
