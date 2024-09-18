"""Misc module"""

import logging

from typing import Union, Any


class Misc:
    """
    Misc methods
    """

    @staticmethod
    def safe_index(array: list[Any], index: int) -> Any:
        """
        Safely retrieves an item from a list by index.

        Args:
            array (List[Any]): The list to retrieve the item from.
            index (int): The index of the item to retrieve.

        Returns:
            Any: The item at the specified index, or None if index is out of range.
        """
        try:
            return array[index]
        except IndexError as e:
            logging.debug(e)
            return None

    @staticmethod
    def set_int(value: str) -> Union[int, str]:
        """
        Converts a string to an integer if possible.

        Args:
            value (str): The string value to convert.

        Returns:
            Union[int, str]: The converted integer value or the original string if conversion fails.
        """
        return int(value) if value.isdigit() else value
