"""HTML parser"""

from urllib.parse import urlparse, parse_qs

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.telegram.parser.misc import Misc


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
        updated_dict = [
            {v[0]: Misc.set_int(v[1][0])} for v in dictionary.items()
        ]
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
            f.css_first(".counter_type")
            .text(): f.css_first(".counter_value")
            .text()
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
        return Misc.safe_index(
            [
                t.attributes["content"]
                for t in self.soup.css("meta")
                if t.attributes.get(selector) == name
            ],
            0,
        )

    def get_labels(self) -> list[str]:
        """
        Extracts and returns a list of label classes
        from the Telegram channel's HTML content.

        Returns:
            list[str]: A list of label class names present in the channel's header.
        """
        return [
            label.attributes.get("class").split("-")[0]
            for label in self.soup.css(".tgme_header_labels>i")
            if label
        ]

    def get_offset(
        self, node: LexborNode, more: bool = False
    ) -> dict[str, int]:
        """
        Parses offset values from HTML link tags.

        Args:
            node (LexborNode): The HTML node to search for link tags.
            more (bool): Is more response

        Returns:
            dict[str, int]: A dictionary containing parsed offset values.
        """
        links = self._get_relevant_links(node, more)
        return self._extract_offset_values(links)

    @staticmethod
    def _get_relevant_links(node: LexborNode, more: bool) -> list[LexborNode]:
        """
        Gets relevant links based on the more flag.

        Args:
            node (LexborNode): The HTML node to search for links
            more (bool): Whether to get more messages links

        Returns:
            list[LexborNode]: List of relevant link nodes
        """
        return node.css("a.tme_messages_more" if more else "link")

    def _extract_offset_values(
        self, links: list[LexborNode]
    ) -> dict[str, int]:
        """
        Extracts offset values from link nodes.

        Args:
            links (list[LexborNode]): List of link nodes to process

        Returns:
            dict[str, int]: Dictionary of extracted offset values
        """
        keys = {}
        for link in links:
            if self._is_relevant_link(link):
                self._process_link(link, keys)
        return keys

    @staticmethod
    def _is_relevant_link(link: LexborNode) -> bool:
        """
        Checks if a link node is relevant for offset extraction.

        Args:
            link (LexborNode): Link node to check

        Returns:
            bool: True if link is relevant, False otherwise
        """
        body = link.attributes.get("rel") in ("prev", "next")
        more = any(
            key in link.attributes.keys()
            for key in ("data-before", "data-after")
        )
        return body or more

    def _process_link(self, link: LexborNode, keys: dict[str, int]) -> None:
        """
        Processes a single link and updates the keys dictionary with offset values.

        Args:
            link (LexborNode): Link node to process
            keys (dict[str, int]): Dictionary to update with offset values
        """
        query_params = self.query(link.attributes.get("href"))
        for k, v in query_params.items():
            keys[k] = v
