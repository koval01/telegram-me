"""
JSON's processors
"""
import logging
from typing import Any, Dict, Set

InputJson = Dict[str, Any]


def clean_json_of_post(json_data: InputJson, media_meta: bool = False) -> Dict[str, Any]:
    """
    Safely cleans JSON data by removing unnecessary fields and simplifying the structure.

    Args:
        json_data: The input JSON data
        media_meta: Whether to return media URLs (default: False)

    Returns:
        Cleaned JSON data
    """
    if not json_data or not isinstance(json_data, dict):
        return {}

    keys_to_remove: Set[str] = {'id', 'meta', 'url', 'thumb', 'unix', 'cover',
                                'to_message', 'raw', 'waves', 'entities', 'avatar', 'view'}
    valid_media_types: Set[str] = {'image', 'video', 'roundvideo', 'gif', 'sticker', 'voice'}

    def process_node(node: Any) -> Any:
        if node is None or not isinstance(node, (dict, list)):
            return node

        if isinstance(node, list):
            return [process_node(item) for item in node]

        result: Dict[str, Any] = {}

        try:
            for key, value in node.items():
                if key in keys_to_remove:
                    continue

                if (isinstance(value, dict) and
                        'string' in value and
                        'html' in value and
                        len(value) == 2):
                    result[key] = value['string']
                    continue

                if (isinstance(value, dict) and
                        'string' in value):
                    cleaned_value = value.copy()
                    if 'html' in cleaned_value:
                        del cleaned_value['html']
                    result[key] = process_node(cleaned_value)
                    continue

                if (media_meta and
                        key == 'media' and
                        isinstance(value, list)):
                    processed_media = []
                    for media_item in value:
                        if not isinstance(media_item, dict):
                            continue

                        media_type = media_item.get('type')
                        if not isinstance(media_type, str) or media_type not in valid_media_types:
                            continue

                        media_url = None
                        if media_type in {'image', 'voice'}:
                            media_url = media_item.get('url')
                        elif media_type in {'video', 'roundvideo', 'gif', 'sticker'}:
                            media_url = media_item.get('thumb')

                        # Handle both string URLs and HttpUrl objects
                        if media_url is not None:
                            if hasattr(media_url, 'unicode_string'):  # For HttpUrl objects
                                media_url = str(media_url.unicode_string())
                            elif isinstance(media_url, str):
                                media_url = media_url
                            else:
                                continue

                            processed_media.append({
                                'url': media_url,
                                'type': media_type
                            })

                    result[key] = processed_media
                    continue

                if isinstance(value, (dict, list)):
                    result[key] = process_node(value)
                else:
                    result[key] = value

        except Exception as e:
            logging.debug(f'Error processing JSON node: {e}')
            return {}

        return result

    try:
        cleaned = process_node(json_data)
        return cleaned if isinstance(cleaned, dict) else {}
    except Exception as error:
        logging.debug(f'Error cleaning JSON: {error}')
        return {}
