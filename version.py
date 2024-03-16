"""
Version of application module
"""

import git


class Version:
    """
    Represents the version information of a Git repository.
    """

    def __init__(self) -> None:
        """
        Initializes a Version object by obtaining the
        latest commit information from the Git repository.
        """
        self.repo = git.Repo()
        self.commit = self.repo.head.commit

    @property
    def hex(self) -> str:
        """
        Returns the abbreviated hexadecimal representation of the latest commit hash.
        """
        return self.commit.hexsha[:7]

    def full_hex(self) -> str:
        """
        Returns the full hexadecimal representation of the latest commit hash.
        """
        return self.commit.hexsha
