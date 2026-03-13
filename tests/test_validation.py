# pylint: disable=missing-function-docstring,duplicate-code

from app.utils.validation import is_valid_channel, validate_channel_or_raise


def test_is_valid_channel_accepts_valid_username() -> None:
    assert is_valid_channel("durov")
    assert is_valid_channel("Telegram_123")


def test_is_valid_channel_rejects_invalid_username() -> None:
    assert not is_valid_channel("_durov")
    assert not is_valid_channel("ab")
    assert not is_valid_channel("durov-chan")


def test_validate_channel_or_raise_strips_value() -> None:
    assert validate_channel_or_raise(" durov ") == "durov"


def test_validate_channel_or_raise_raises_value_error() -> None:
    try:
        validate_channel_or_raise("___")
    except ValueError:
        return
    raise AssertionError("Expected ValueError for invalid channel")
