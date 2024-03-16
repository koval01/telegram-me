import git


class Version:
    hex: str = "0000000"

    def __init__(self) -> None:
        self.repo = git.Repo()
        self.commit = self.repo.head.commit
        Version.hex = self.commit.hexsha[:7]
