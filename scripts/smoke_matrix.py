#!/usr/bin/env python3
"""Extended smoke matrix for local/API contract verification.

Besides status codes, the main successful responses are checked for content
(`Check`): a change of the t.me markup that the parser no longer understands
usually still answers 200, just with empty posts or titles.

The strict scenarios only ask for pages recorded in tests/fixtures/telegram,
so the matrix also passes against the fake t.me of the integration and deploy
tests (tests/integration/fake_telegram.py). Standard library only: the deploy
runs it inside the production container.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib import error, request

# Inspects a parsed 200 response; returns what is wrong with it, or None.
type Check = Callable[[Any], str | None]


@dataclass
class Scenario:
    """Single API smoke scenario."""

    name: str
    method: str
    path: str
    expected_statuses: set[int]
    payload: dict[str, Any] | list[Any] | None = None
    strict: bool = True
    check: Check | None = None
    # For pages that are not JSON: text a 200 response must contain.
    contains: str | None = None
    # A 403 counts only as Cloudflare's firewall block (error 1020).
    edge_block: bool = False


@dataclass
class ScenarioResult:
    """Result for one executed scenario."""

    scenario: Scenario
    status: int | str
    elapsed_ms: int
    body_size: int
    body_preview: str
    problem: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when status and content match the expected contract."""
        return (
            isinstance(self.status, int)
            and self.status in self.scenario.expected_statuses
            and self.problem is None
        )


def _get(data: Any, *path: str) -> Any:
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _post_problem(post: Any) -> str | None:
    """What a parsed post lacks: an id, a date, or any content."""
    if not isinstance(_get(post, "id"), int) or post["id"] <= 0:
        return "a post without a valid id"
    if not isinstance(_get(post, "footer", "date", "unix"), int):
        return f"post {post['id']} without a date"
    content = _get(post, "content") or {}
    if not any(content.get(kind) for kind in ("text", "media", "poll", "preview_link")):
        return f"post {post['id']} without text, media, poll or link preview"
    return None


def _posts_problem(
    posts: Any, *, before: int | None = None, after: int | None = None
) -> str | None:
    if not isinstance(posts, list) or not posts:
        return "no posts"
    problems = [problem for post in posts if (problem := _post_problem(post))]
    # A few posts may be empty for real (service messages); most must not.
    if len(problems) * 2 > len(posts):
        return f"{len(problems)} of {len(posts)} posts are incomplete, e.g. {problems[0]}"
    ids = [post["id"] for post in posts if isinstance(_get(post, "id"), int)]
    if before is not None and any(i >= before for i in ids):
        return f"posts at or after {before} in a page before it"
    if after is not None and any(i <= after for i in ids):
        return f"posts at or before {after} in a page after it"
    return None


def channel_page(channel: str) -> Check:
    """`/v1/body/<channel>`: the channel's identity and a page of complete posts."""

    def check(body: Any) -> str | None:
        username = _get(body, "channel", "username")
        if not isinstance(username, str) or username.lower() != channel.lower():
            return f"channel.username is {username!r}, not {channel!r}"
        if not _get(body, "channel", "title", "string"):
            return "channel without a title"
        return _posts_problem(_get(body, "content", "posts"))

    return check


def more_page(direction: str, position: int) -> Check:
    """`/v1/more/...`: complete posts on the requested side of `position`."""

    def check(body: Any) -> str | None:
        side = {"before": position} if direction == "before" else {"after": position}
        return _posts_problem(_get(body, "posts"), **side)

    return check


def single_post(post_id: int) -> Check:
    """`/v1/post/...`: the requested post, complete."""

    def check(body: Any) -> str | None:
        if _get(body, "id") != post_id:
            return f"post id is {_get(body, 'id')!r}, not {post_id}"
        return _post_problem(body)

    return check


def preview(body: Any) -> str | None:
    """`/v1/preview/...`: a channel card with a title."""
    return None if _get(body, "channel", "title") else "preview without a channel title"


def previews(channels: list[str]) -> Check:
    """`/v1/previews`: a card with a title for each channel that exists."""

    def check(body: Any) -> str | None:
        if not isinstance(body, dict) or not body:
            return "no previews"
        for channel in channels:
            if channel in body and not _get(body, channel, "channel", "title"):
                return f"preview of {channel} without a channel title"
        return None

    return check


def feed(body: Any) -> str | None:
    """`/v1/feed`: ranked, complete posts."""
    result = _get(body, "result")
    if not isinstance(result, list) or not result:
        return "empty feed"
    if any(not isinstance(_get(item, "_score"), int | float) for item in result):
        return "feed item without a score"
    return _posts_problem(result)


def graphql_ok(body: Any) -> str | None:
    """A GraphQL result (or batch of results) with data and without errors."""
    results = body if isinstance(body, list) else [body]
    for result in results:
        if _get(result, "errors"):
            return f"GraphQL errors: {str(result['errors'])[:120]}"
        if not _get(result, "data"):
            return "GraphQL result without data"
    return None


# Cloudflare's error code when a firewall rule (or schema validation) blocks a request.
CLOUDFLARE_BLOCK = 1020
# Sent with every request (--header), e.g. the key of a Cloudflare WAF skip rule.
EXTRA_HEADERS: dict[str, str] = {}


def solve_pow(challenge: str, difficulty: int, method: str, target: str) -> str:
    """Find a nonce for a proof-of-work challenge (docs/api/protection.md)."""
    prefix = hashlib.sha256(f"{challenge}\n".encode())
    suffix = f"\n{method} {target}".encode()
    for n in itertools.count():
        nonce = format(n, "x")
        digest = prefix.copy()
        digest.update(nonce.encode() + suffix)
        if int.from_bytes(digest.digest(), "big") >> (256 - difficulty) == 0:
            return nonce
    raise AssertionError


def pow_challenge(raw: bytes) -> dict[str, Any] | None:
    """The fresh challenge in a proof-of-work rejection, if it is one."""
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    challenge = body.get("pow") if isinstance(body, dict) else None
    return challenge if isinstance(challenge, dict) and "challenge" in challenge else None


def call(base_url: str, scenario: Scenario, timeout: float) -> ScenarioResult:
    """Execute one HTTP request scenario; answers a proof-of-work demand once."""
    result, challenge = _call(base_url, scenario, timeout, {})
    if challenge is None:
        return result
    text, bits = challenge["challenge"], challenge["difficulty"]
    nonce = solve_pow(text, bits, scenario.method, scenario.path)
    result, _ = _call(base_url, scenario, timeout, {"X-PoW": f"{text}:{nonce}"})
    return result


def _call(
    base_url: str, scenario: Scenario, timeout: float, extra: dict[str, str]
) -> tuple[ScenarioResult, dict[str, Any] | None]:
    url = f"{base_url}{scenario.path}"
    headers = {"accept": "application/json", **EXTRA_HEADERS, **extra}
    data = None
    if scenario.payload is not None:
        data = json.dumps(scenario.payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    # base_url is checked to be http(s) in main().
    req = request.Request(url, method=scenario.method, headers=headers, data=data)  # noqa: S310
    started = time.time()

    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
            elapsed = int((time.time() - started) * 1000)
            preview = raw.decode("utf-8", errors="replace")[:200].replace("\n", " ")
            problem = _content_problem(scenario, response.status, raw)
            result = ScenarioResult(scenario, response.status, elapsed, len(raw), preview, problem)
            return result, None
    except error.HTTPError as exc:
        with exc:  # the error holds the response open until closed
            raw = exc.read()
        elapsed = int((time.time() - started) * 1000)
        preview = raw.decode("utf-8", errors="replace")[:200].replace("\n", " ")
        challenge = pow_challenge(raw) if exc.code in {400, 403, 428} and not extra else None
        problem = None
        if scenario.edge_block and exc.code == 403 and not _cloudflare_block(raw):
            problem = "403 is not a Cloudflare firewall block"
        result = ScenarioResult(scenario, exc.code, elapsed, len(raw), preview, problem)
        return result, challenge
    except Exception as exc:  # noqa: BLE001 - every failure is a row of the report
        elapsed = int((time.time() - started) * 1000)
        result = ScenarioResult(
            scenario=scenario,
            status="ERR",
            elapsed_ms=elapsed,
            body_size=0,
            body_preview=f"{type(exc).__name__}: {exc}",
        )
        return result, None


def _cloudflare_block(raw: bytes) -> bool:
    """Whether a body is Cloudflare's firewall block: JSON for JSON clients, else text."""
    try:
        body = json.loads(raw)
    except ValueError:
        return f"error code: {CLOUDFLARE_BLOCK}".encode() in raw
    return (
        isinstance(body, dict)
        and body.get("cloudflare_error") is True
        and body.get("error_code") == CLOUDFLARE_BLOCK
    )


def _content_problem(scenario: Scenario, status: int, raw: bytes) -> str | None:
    if status != 200:
        return None
    if scenario.contains is not None:
        text = raw.decode("utf-8", errors="replace")
        return None if scenario.contains in text else f"no {scenario.contains!r} in the page"
    if scenario.check is None:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return "response is not JSON"
    return scenario.check(body)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Run extended API smoke matrix")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Base API URL")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--preset",
        choices=("auto", "full", "minimal"),
        default="auto",
        help="Feature preset expectation",
    )
    parser.add_argument(
        "--exploratory-channels",
        default="nebudetafk,nebudetgg,dovolllnaya,zalupaonline,stranaua,telegram,durov",
        help="Comma-separated channels for broad extra checks",
    )
    parser.add_argument(
        "--edge-blocks-invalid",
        action="store_true",
        help=(
            "Invalid requests (expect 422) may also get Cloudflare's firewall block "
            "(403, error 1020): schema validation at the edge rejects them before the API"
        ),
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="Extra header for every request, e.g. a Cloudflare skip-rule key (repeatable)",
    )
    args = parser.parse_args()
    for header in args.header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            parser.error(f"--header must be NAME:VALUE, got {header!r}")
        EXTRA_HEADERS[name.strip()] = value.strip()
    return args


def detect_feature_state(base_url: str, timeout: float) -> tuple[bool, bool]:
    """Detect whether feed/previews are enabled on the running instance."""
    feed_probe = Scenario(
        name="probe_feed",
        method="POST",
        path="/v1/feed",
        payload={"channels": ["durov"]},
        expected_statuses={200, 503},
    )
    previews_probe = Scenario(
        name="probe_previews",
        method="POST",
        path="/v1/previews",
        payload={"channels": ["durov"]},
        expected_statuses={200, 503},
    )
    feed_enabled = call(base_url, feed_probe, timeout).status == 200
    previews_enabled = call(base_url, previews_probe, timeout).status == 200
    return feed_enabled, previews_enabled


def detect_graphql(base_url: str, timeout: float) -> bool:
    """Detect whether the GraphQL endpoint is mounted on the running instance."""
    probe = Scenario(
        name="probe_graphql",
        method="POST",
        path="/graphql",
        payload={"query": "{ __typename }"},
        expected_statuses={200, 404},
    )
    return call(base_url, probe, timeout).status == 200


def graphql_scenarios(enabled: bool) -> list[Scenario]:
    """GraphQL checks: 404 everywhere while the endpoint is disabled."""
    body = '{ body(channel: "durov") { channel { username } meta { offset { before } } } }'
    post = '{ post(channel: "stranaua", id: 252483) { id content { text { string } } } }'
    return [
        Scenario(
            "graphql_body",
            "POST",
            "/graphql",
            {200 if enabled else 404},
            {"query": body},
            check=graphql_ok,
        ),
        Scenario(
            "graphql_batch",
            "POST",
            "/graphql",
            {200 if enabled else 404},
            payload=[{"query": body}, {"query": post}],
            check=graphql_ok,
        ),
        Scenario(
            "graphql_batch_too_large",
            "POST",
            "/graphql",
            {400 if enabled else 404},
            payload=[{"query": "{ __typename }"}] * 11,
        ),
        Scenario("graphql_no_query", "POST", "/graphql", {400 if enabled else 404}, payload={}),
    ]


def edge_blocks_invalid(scenarios: list[Scenario]) -> list[Scenario]:
    """Let the input-validation scenarios (422) pass on a Cloudflare firewall block too."""
    return [
        replace(scenario, expected_statuses={422, 403}, edge_block=True)
        if scenario.expected_statuses == {422}
        else scenario
        for scenario in scenarios
    ]


def build_scenarios(
    feed_enabled: bool, previews_enabled: bool, exploratory_channels: list[str]
) -> list[Scenario]:
    """Build strict and exploratory smoke scenarios."""
    strict_channels = ["durov", "telegram", "stranaua"]

    scenarios: list[Scenario] = [
        Scenario("health_ok", "GET", "/healthz", {200}),
        Scenario("landing_ok", "GET", "/", {200}, contains='id="try-form"'),
        Scenario("api_reference_ok", "GET", "/api", {200}, contains="SwaggerUIBundle"),
        Scenario("openapi_ok", "GET", "/openapi.json", {200}),
        Scenario("robots_ok", "GET", "/robots.txt", {200}, contains="Disallow: /v1/"),
        Scenario("sitemap_ok", "GET", "/sitemap.xml", {200}, contains="<urlset"),
        # Served from the image; redirects to GitHub Pages where it has no docs.
        Scenario("docs_ok", "GET", "/docs/", {200}, contains="TelegramMe"),
        Scenario("body_invalid_short", "GET", "/v1/body/ab", {422}),
        Scenario("body_invalid_chars", "GET", "/v1/body/durov-chan", {422}),
        Scenario("body_not_found_like", "GET", "/v1/body/zzzzzznotexistingchannelzzzz", {404}),
        Scenario("more_invalid_direction", "GET", "/v1/more/stranaua/sideways/252483", {422}),
        Scenario("more_invalid_position", "GET", "/v1/more/stranaua/after/0", {422}),
        Scenario("post_invalid_nonint", "GET", "/v1/post/stranaua/notint", {422}),
        Scenario("post_invalid_too_big", "GET", "/v1/post/stranaua/10000001", {422}),
        Scenario("preview_invalid_channel", "GET", "/v1/preview/bad-name", {422}),
        Scenario(
            "preview_not_found_like",
            "GET",
            "/v1/preview/zzzzzznotexistingchannelzzzz",
            {404} if previews_enabled else {503},
        ),
        Scenario(
            "previews_invalid_array_legacy",
            "POST",
            "/v1/previews",
            {422},
            payload=["durov", "stranaua"],
        ),
        Scenario("previews_invalid_empty", "POST", "/v1/previews", {422}, payload={"channels": []}),
        Scenario(
            "previews_invalid_item",
            "POST",
            "/v1/previews",
            {422},
            payload={"channels": ["durov", "bad-name"]},
        ),
        Scenario(
            "previews_invalid_too_many",
            "POST",
            "/v1/previews",
            {422},
            payload={"channels": [f"chan{i:03d}" for i in range(101)]},
        ),
        Scenario("feed_invalid_empty", "POST", "/v1/feed", {422}, payload={"channels": []}),
        Scenario(
            "feed_invalid_item",
            "POST",
            "/v1/feed",
            {422},
            payload={"channels": ["durov", "bad-name"]},
        ),
        Scenario(
            "feed_invalid_missing", "POST", "/v1/feed", {422}, payload={"not_channels": ["durov"]}
        ),
    ]

    for channel in strict_channels:
        scenarios.append(
            Scenario(
                f"body_valid_{channel}",
                "GET",
                f"/v1/body/{channel}",
                {200},
                check=channel_page(channel),
            )
        )
        scenarios.append(
            Scenario(
                f"preview_valid_{channel}",
                "GET",
                f"/v1/preview/{channel}",
                {200 if previews_enabled else 503},
                check=preview,
            )
        )

    scenarios.extend(
        [
            Scenario(
                "more_after_valid",
                "GET",
                "/v1/more/stranaua/after/252483",
                {200},
                check=more_page("after", 252483),
            ),
            Scenario(
                "more_before_valid",
                "GET",
                "/v1/more/stranaua/before/252483",
                {200},
                check=more_page("before", 252483),
            ),
            Scenario(
                "post_valid",
                "GET",
                "/v1/post/stranaua/252483",
                {200},
                check=single_post(252483),
            ),
            Scenario(
                "previews_valid",
                "POST",
                "/v1/previews",
                {200 if previews_enabled else 503},
                payload={"channels": ["durov", "stranaua", "telegram"]},
                check=previews(["durov", "stranaua", "telegram"]),
            ),
            Scenario(
                "previews_partial_result",
                "POST",
                "/v1/previews",
                {200 if previews_enabled else 503},
                payload={"channels": ["durov", "zzzzzznotexistingchannelzzzz"]},
            ),
            Scenario(
                "post_with_inline_button",
                "GET",
                "/v1/post/nebudetgg/6209",
                {200},
                check=single_post(6209),
            ),
            Scenario(
                "body_around_post",
                "GET",
                "/v1/body/stranaua?position=252483",
                {200},
                check=channel_page("stranaua"),
            ),
            Scenario(
                "feed_valid",
                "POST",
                "/v1/feed",
                {200 if feed_enabled else 503},
                payload={"channels": ["durov", "stranaua", "nebudetafk"]},
                check=feed,
            ),
        ]
    )

    # Exploratory scenarios: broader channel coverage, non-strict but useful signal.
    for channel in exploratory_channels:
        scenarios.append(
            Scenario(
                name=f"explore_body_{channel}",
                method="GET",
                path=f"/v1/body/{channel}",
                expected_statuses={200, 404},
                strict=False,
            )
        )
        scenarios.append(
            Scenario(
                name=f"explore_preview_{channel}",
                method="GET",
                path=f"/v1/preview/{channel}",
                expected_statuses={200, 404, 503},
                strict=False,
            )
        )

    return scenarios


def print_results(results: list[ScenarioResult]) -> None:
    """Print table-like output for easy scanning."""
    print("name|strict|method|path|status|ms|bytes|ok|problem|preview")
    for result in results:
        print(
            "|".join(
                [
                    result.scenario.name,
                    str(result.scenario.strict),
                    result.scenario.method,
                    result.scenario.path,
                    str(result.status),
                    str(result.elapsed_ms),
                    str(result.body_size),
                    str(result.ok),
                    result.problem or "",
                    result.body_preview,
                ]
            )
        )


def main() -> int:
    """Run full smoke matrix and return process exit code."""
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit("--base-url must start with http:// or https://")
    exploratory_channels = [c.strip() for c in args.exploratory_channels.split(",") if c.strip()]

    detected_feed_enabled, detected_previews_enabled = detect_feature_state(base_url, args.timeout)

    if args.preset == "auto":
        expected_feed_enabled, expected_previews_enabled = (
            detected_feed_enabled,
            detected_previews_enabled,
        )
    elif args.preset == "full":
        expected_feed_enabled, expected_previews_enabled = True, True
    else:
        expected_feed_enabled, expected_previews_enabled = False, False

    # Contract checks should follow real runtime behavior.
    scenarios = build_scenarios(
        detected_feed_enabled,
        detected_previews_enabled,
        exploratory_channels,
    )
    scenarios.extend(graphql_scenarios(detect_graphql(base_url, args.timeout)))
    if args.edge_blocks_invalid:
        scenarios = edge_blocks_invalid(scenarios)
    results = [call(base_url, scenario, args.timeout) for scenario in scenarios]
    print_results(results)

    strict_failures = [result for result in results if result.scenario.strict and not result.ok]
    exploratory_failures = [
        result for result in results if (not result.scenario.strict) and not result.ok
    ]

    print("\nSUMMARY")
    print(f"  strict_total={len([r for r in results if r.scenario.strict])}")
    print(f"  strict_failures={len(strict_failures)}")
    print(f"  exploratory_total={len([r for r in results if not r.scenario.strict])}")
    print(f"  exploratory_failures={len(exploratory_failures)}")
    print(f"  detected_feed_enabled={detected_feed_enabled}")
    print(f"  detected_previews_enabled={detected_previews_enabled}")
    print(f"  expected_feed_enabled={expected_feed_enabled}")
    print(f"  expected_previews_enabled={expected_previews_enabled}")

    preset_mismatch = args.preset != "auto" and (
        detected_feed_enabled != expected_feed_enabled
        or detected_previews_enabled != expected_previews_enabled
    )
    if preset_mismatch:
        print("\nPRESET MISMATCH")
        print(
            "  Runtime feature state does not match selected preset. "
            "Check ENV flags used to start the server."
        )
        print(f"  expected: feed={expected_feed_enabled}, previews={expected_previews_enabled}")
        print(f"  detected: feed={detected_feed_enabled}, previews={detected_previews_enabled}")

    if strict_failures:
        print("\nSTRICT FAILURES")
        for result in strict_failures:
            print(
                f"- {result.scenario.name}: status={result.status}, "
                f"expected={sorted(result.scenario.expected_statuses)}, path={result.scenario.path}"
                + (f", problem: {result.problem}" if result.problem else "")
            )
        return 1

    if preset_mismatch:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
