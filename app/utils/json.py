"""
JSON's processors
"""
import logging
from typing import Any, Dict, List, Set, Optional

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

    try:
        cleaned = process_node(json_data, media_meta)
        return cleaned if isinstance(cleaned, dict) else {}
    except (AttributeError, TypeError, ValueError) as error:
        logging.debug('Error cleaning JSON: %s', error)
        return {}


def process_node(node: Any, media_meta: bool) -> Any:
    """Process a single node in the JSON structure."""
    if node is None or not isinstance(node, (dict, list)):
        return node

    if isinstance(node, list):
        return [process_node(item, media_meta) for item in node]

    result: Dict[str, Any] = {}

    try:
        for key, value in node.items():
            if key in get_keys_to_remove():
                continue

            processed_value = process_value(key, value, media_meta)
            if processed_value is not None:
                result[key] = processed_value

    except (AttributeError, TypeError, ValueError) as e:
        logging.debug('Error processing JSON node: %s', e)
        return {}

    return result


def get_keys_to_remove() -> Set[str]:
    """Return set of keys to remove from JSON."""
    return {'id', 'meta', 'url', 'thumb', 'unix', 'cover',
            'to_message', 'raw', 'waves', 'entities', 'avatar', 'view'}


def process_value(key: str, value: Any, media_meta: bool) -> Optional[Any]:
    """Process a single value in the JSON structure."""
    if (isinstance(value, dict) and 'string' in value and 'html' in value and len(value) == 2):
        return value['string']

    if isinstance(value, dict) and 'string' in value:
        cleaned_value = value.copy()
        if 'html' in cleaned_value:
            del cleaned_value['html']
        return process_node(cleaned_value, media_meta)

    if media_meta and key == 'media' and isinstance(value, list):
        return process_media_list(value)

    if isinstance(value, (dict, list)):
        return process_node(value, media_meta)

    return value


def process_media_list(media_list: List[Any]) -> List[Dict[str, Any]]:
    """Process media list in the JSON structure."""
    valid_media_types = {'image', 'video', 'roundvideo', 'gif', 'sticker', 'voice'}
    processed_media = []

    for media_item in media_list:
        if not isinstance(media_item, dict):
            continue

        media_type = media_item.get('type')
        if not isinstance(media_type, str) or media_type not in valid_media_types:
            continue

        media_url = get_media_url(media_item, media_type)
        if media_url is not None:
            processed_media.append({
                'url': media_url,
                'type': media_type
            })

    return processed_media


def get_media_url(media_item: Dict[str, Any], media_type: str) -> Optional[str]:
    """Extract media URL from a media item."""
    if media_type in {'image', 'voice'}:
        url = media_item.get('url')
    elif media_type in {'video', 'roundvideo', 'gif', 'sticker'}:
        url = media_item.get('thumb')
    else:
        return None

    if hasattr(url, 'unicode_string'):  # For HttpUrl objects
        return str(url.unicode_string())
    if isinstance(url, str):
        return url
    return None
