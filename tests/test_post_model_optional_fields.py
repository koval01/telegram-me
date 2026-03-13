# pylint: disable=missing-function-docstring

from app.telegram.models.post import Post


def test_post_model_allows_missing_optional_nested_fields() -> None:
    payload = {
        "id": 1,
        "content": {
            "preview_link": {
                "url": "https://example.com",
                "title": "Example",
                "site_name": "Example",
                "description": {"string": "desc", "html": "desc"},
            },
            "reply": {
                "name": {"string": "Author", "html": "Author"},
                "text": {"string": "Reply text", "html": "Reply text"},
                "to_message": 10,
            },
        },
        "footer": {"date": {"string": "2026-03-13T09:00:00+0000", "unix": 1710310800}},
        "view": "100",
    }

    model = Post.model_validate(payload)
    dumped = model.model_dump(exclude_none=True)

    assert "thumb" not in dumped["content"]["preview_link"]
    assert "cover" not in dumped["content"]["reply"]
