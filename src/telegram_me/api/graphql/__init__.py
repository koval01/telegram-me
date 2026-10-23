"""GraphQL API (`/graphql`) over the same services as the REST API."""

from strawberry.fastapi import GraphQLRouter

from telegram_me.api.graphql.context import GraphQLContext, get_context
from telegram_me.api.graphql.schema import build_schema
from telegram_me.settings import Settings

PATH = "/graphql"


def create_router(settings: Settings) -> GraphQLRouter[GraphQLContext, None]:
    """Router serving `/graphql`.

    Queries are accepted via POST (single or batched). With docs enabled,
    GET serves GraphiQL in browsers and also accepts queries; with
    `DISABLE_DOCS` the IDE, GET queries and introspection are all off.
    """
    docs = not settings.disable_docs
    return GraphQLRouter(
        build_schema(settings),
        path=PATH,
        graphql_ide="graphiql" if docs else None,
        allow_queries_via_get=docs,
        context_getter=get_context,
        tags=["GraphQL"],
    )
