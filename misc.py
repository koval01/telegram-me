"""Global misc module"""

import logging


def write_to_file(filename: str, content: str) -> None:
    """
    Writes content to a file.

    Args:
        filename (str): The name of the file to write to.
        content (str): The content to write to the file.

    Returns:
        None

    Raises:
        IOError: If an error occurs while writing to the file.
    """
    try:
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(content)
    except IOError as e:
        logging.error("Error writing data to file: %s", e)
