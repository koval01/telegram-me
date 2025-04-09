"""
Model for channel previews data
"""

from typing import Dict, Optional

from pydantic import RootModel

from app.telegram.models.preview import Preview


# pylint: disable=all
class Previews(RootModel[Dict[str, Optional[Preview]]]):
    """
    Represents previews of multiple Telegram channels.

    The model uses a dictionary to map unique identifiers to their corresponding channel previews.
    Empty dictionary is allowed.
    """

    def model_dump(self, *args, **kwargs) -> Dict[str, Dict]:
        raw_data = super().model_dump(*args, **kwargs)

        filtered_data = {
            key: value
            for key, value in raw_data.items()
            if value and value.get("channel") is not None
        }

        return filtered_data
