# pylint: disable=missing-function-docstring

from app.telegram.parser.types.entities import EntitiesParser


def test_entities_parser_keeps_link_url() -> None:
    html = '<a href="https://example.com" onclick="return false;">example</a>'
    entities = EntitiesParser(html).parse_message()

    assert entities
    assert entities[0]["type"] in {"url", "text_link"}
    assert entities[0].get("url") == "https://example.com"
