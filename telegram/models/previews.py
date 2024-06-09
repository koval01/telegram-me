"""
Model for channel previews data
"""

from typing import Dict

from pydantic import RootModel

from telegram.models.preview import Preview


# pylint: disable=all
class Previews(RootModel[Dict[str, Preview]]):
    """
    Represents previews of multiple Telegram channels.

    The model uses a dictionary to map unique identifiers to their corresponding channel previews.
    """
