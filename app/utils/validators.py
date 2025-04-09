"""
Validators
"""
import re


def check_username(username: str) -> bool:
    """
    Validate the given username based on a predefined pattern.

    The username must:
    - Start with a letter (A-Z, a-z)
    - Contain only letters, digits, and underscores (A-Z, a-z, 0-9, _)
    - Be between 5 and 32 characters in length (inclusive)

    Args:
        username (str): The username to validate.

    Returns:
        bool: True if the username matches the pattern, False otherwise.
    """
    pattern = r'^[A-Za-z][A-Za-z0-9_]{4,31}$'
    return bool(re.match(pattern, username))


class PydanticValidator:
    """
    A class that provides validation methods for usernames.
    """

    @staticmethod
    def check_valid_username(username: str) -> str:
        """
        Check if the given username is valid.

        Raises a ValueError if the username is invalid.

        Args:
            username (str): The username to validate.

        Returns:
            str: The username if it is valid.

        Raises:
            ValueError: If the username is invalid.
        """
        if not check_username(username):
            raise ValueError(f'invalid username "{username}"')
        return username

    @classmethod
    def check_valid_usernames(cls, usernames: list[str]) -> list[str]:
        """
        Validate a list of usernames.

        This method calls `check_valid_username` for each username in the list.
        If any username is invalid, a ValueError is raised.

        Args:
            usernames (list[str]): A list of usernames to validate.

        Returns:
            list[str]: The list of usernames if all are valid.

        Raises:
            ValueError: If any username in the list is invalid.
        """
        for username in usernames:
            cls.check_valid_username(username)
        return usernames
