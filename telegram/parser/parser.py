"""HTML parser"""

from urllib.parse import urlparse, parse_qs

from selectolax.lexbor import LexborHTMLParser, LexborNode

from telegram.parser.misc import Misc


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
    def query(url: str) -> dict[str, int]:
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
    def get_counters(node: list[LexborNode]) -> dict[str, str]:
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
