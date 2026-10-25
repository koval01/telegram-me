"""Names in src/ that are used, but not in a way vulture can see.

vulture (`make lint`, settings in pyproject.toml) reads this file with src/.
Add a name here only with the reason it is used.
"""

from telegram_me.api.protection import PowChallenge
from telegram_me.cache import PeerCache
from telegram_me.server import asgi
from telegram_me.settings import Settings

# Response model of GET /v1/pow/challenge: pydantic serialises the fields.
PowChallenge.difficulty
PowChallenge.algorithm
PowChallenge.header
PowChallenge.input

# Granian imports the app factory by name ("telegram_me.server:asgi").
asgi

# The `Cache` protocol's backend name, for diagnostics like the other backends'.
PeerCache.backend

# Read by pydantic-settings.
Settings.model_config
