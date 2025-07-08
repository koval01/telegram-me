from pydantic import BaseModel


class ParsedAndRaw(BaseModel):
    """Represents text content in both processed and original formats.

    This model is used to store text that has been parsed/processed (string)
    alongside its original raw HTML representation. Useful for maintaining
    both display-ready and original versions of formatted text.

    Attributes:
        string (str): The processed/parsed text content with formatting removed
            or simplified for display purposes.
        html (str): The original raw HTML content with all formatting preserved,
            exactly as received from the source.

    Example:
        >>> data = ParsedAndRaw(
        ...     string="Hello world",
        ...     html="<b>Hello</b> <i>world</i>"
        ... )
    """
    string: str
    html: str
