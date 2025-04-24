from fastapi import Path, Query


def channel_param():
    """Return a standardized Path parameter for channel name"""
    return Path(description="Telegram channel username.")


def position_path_param():
    """Return a standardized Path parameter for position"""
    return Path(description="History position")


def position_query_param():
    """Return a standardized Query parameter for optional position"""
    return Query(None, description="History position")


def direction_param():
    """Return a standardized Path parameter for direction"""
    return Path(
        description="History load direction"
    )


def identifier_param():
    """Return a standardized Path parameter for post identifier"""
    return Path(description="Post identifier")
