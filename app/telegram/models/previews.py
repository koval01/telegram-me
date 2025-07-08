from typing import Dict, Optional

from pydantic import RootModel

from app.telegram.models.preview import Preview


class Previews(RootModel[Dict[str, Optional[Preview]]]):
    """A collection of Telegram channel previews indexed by unique channel identifiers.

    This model extends Pydantic's RootModel to represent a dictionary mapping where:
    - Keys are unique string identifiers (typically channel usernames or IDs)
    - Values are Optional[Preview] objects containing channel metadata

    The model includes custom serialization logic to filter out None values and empty previews
    during export.

    Example:
        ```python
        {
            "channel1": Preview(...),
            "channel2": None,  # Will be filtered out in model_dump
            "channel3": Preview(...)
        }
        ```

    Methods:
        model_dump: Returns a filtered dictionary with only valid Preview objects
    """

    def model_dump(self, *args, **kwargs) -> Dict[str, Dict]:
        """Serializes the model while filtering invalid entries.

        Performs two levels of filtering:
        1. Removes None values completely
        2. Removes Preview objects where the 'channel' attribute is None

        Args:
            *args: Standard Pydantic model_dump arguments
            **kwargs: Standard Pydantic model_dump keyword arguments

        Returns:
            Dict[str, Dict]: A cleaned dictionary containing only valid channel previews
                            with their complete data structure

        Note:
            The output is suitable for JSON serialization and API responses
        """
        raw_data = super().model_dump(*args, **kwargs)

        filtered_data = {
            key: value
            for key, value in raw_data.items()
            if value and value.get("channel") is not None
        }

        return filtered_data
