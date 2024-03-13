from telegram.requests import Requests
from telegram.parser import Body


class Telegram:

    def __init__(self) -> None:
        pass

    @staticmethod
    async def body(channel: str, position: int = 0) -> dict:
        response = await Requests().body(channel, position)
        if not response:
            return {}

        return await Body(response).get()
