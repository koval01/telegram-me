"""The REST contract as a whole: the OpenAPI document with every feature on.

Pinned in tests/snapshots/openapi.json (`make snapshots` rewrites it), which
`scripts/api_contract.py` compares with the base of a pull request to catch
breaking changes; the GraphQL schema is pinned the same way by
`test_graphql_contract.py::test_schema_sdl`.
"""

import secrets
from typing import Any

from tests.conftest import ClientFactory, Snapshot

# Everything that adds operations or responses to the document.
ALL_FEATURES: dict[str, Any] = {
    "enable_feed": True,
    "enable_previews": True,
    "enable_graphql": True,
    "enable_rate_limit": True,
    "enable_pow": True,
    "pow_secret": secrets.token_hex(32),
}


def test_openapi_document(make_client: ClientFactory, snapshot: Snapshot) -> None:
    document = make_client(**ALL_FEATURES).get("/openapi.json").json()
    # The version changes with every release, not the contract.
    document["info"].pop("version")
    snapshot("openapi", document)
