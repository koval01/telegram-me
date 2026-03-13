#!/usr/bin/env python3
"""Extended smoke matrix for local/API contract verification."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass
class Scenario:
    """Single API smoke scenario."""

    name: str
    method: str
    path: str
    expected_statuses: set[int]
    payload: dict[str, Any] | list[Any] | None = None
    strict: bool = True


@dataclass
class ScenarioResult:
    """Result for one executed scenario."""

    scenario: Scenario
    status: int | str
    elapsed_ms: int
    body_size: int
    body_preview: str

    @property
    def ok(self) -> bool:
        """Return True when status matches expected contract."""
        return isinstance(self.status, int) and self.status in self.scenario.expected_statuses


def call(base_url: str, scenario: Scenario, timeout: float) -> ScenarioResult:
    """Execute one HTTP request scenario."""
    url = f"{base_url}{scenario.path}"
    headers = {"accept": "application/json"}
    data = None
    if scenario.payload is not None:
        data = json.dumps(scenario.payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url, method=scenario.method, headers=headers, data=data)
    started = time.time()

    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            elapsed = int((time.time() - started) * 1000)
            preview = raw.decode("utf-8", errors="replace")[:200].replace("\n", " ")
            return ScenarioResult(scenario, response.status, elapsed, len(raw), preview)
    except error.HTTPError as exc:
        raw = exc.read()
        elapsed = int((time.time() - started) * 1000)
        preview = raw.decode("utf-8", errors="replace")[:200].replace("\n", " ")
        return ScenarioResult(scenario, exc.code, elapsed, len(raw), preview)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        elapsed = int((time.time() - started) * 1000)
        return ScenarioResult(
            scenario=scenario,
            status="ERR",
            elapsed_ms=elapsed,
            body_size=0,
            body_preview=f"{type(exc).__name__}: {exc}",
        )


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
    return parser.parse_args()


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


def build_scenarios(feed_enabled: bool, previews_enabled: bool, exploratory_channels: list[str]) -> list[Scenario]:
    """Build strict and exploratory smoke scenarios."""
    strict_channels = ["durov", "telegram", "stranaua"]

    scenarios: list[Scenario] = [
        Scenario("health_ok", "GET", "/healthz", {200}),
        Scenario("docs_root_ok", "GET", "/", {200}),
        Scenario("openapi_ok", "GET", "/openapi.json", {200}),
        Scenario("body_invalid_short", "GET", "/v1/body/ab", {422}),
        Scenario("body_invalid_chars", "GET", "/v1/body/durov-chan", {422}),
        Scenario("body_not_found_like", "GET", "/v1/body/zzzzzznotexistingchannelzzzz", {404}),
        Scenario("more_invalid_direction", "GET", "/v1/more/stranaua/sideways/229311", {422}),
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
        Scenario("previews_invalid_array_legacy", "POST", "/v1/previews", {422}, payload=["durov", "stranaua"]),
        Scenario("previews_invalid_empty", "POST", "/v1/previews", {422}, payload={"channels": []}),
        Scenario("previews_invalid_item", "POST", "/v1/previews", {422}, payload={"channels": ["durov", "bad-name"]}),
        Scenario(
            "previews_invalid_too_many",
            "POST",
            "/v1/previews",
            {422},
            payload={"channels": [f"chan{i:03d}" for i in range(101)]},
        ),
        Scenario("feed_invalid_empty", "POST", "/v1/feed", {422}, payload={"channels": []}),
        Scenario("feed_invalid_item", "POST", "/v1/feed", {422}, payload={"channels": ["durov", "bad-name"]}),
        Scenario("feed_invalid_missing", "POST", "/v1/feed", {422}, payload={"not_channels": ["durov"]}),
    ]

    for channel in strict_channels:
        scenarios.append(Scenario(f"body_valid_{channel}", "GET", f"/v1/body/{channel}", {200}))
        scenarios.append(Scenario(f"preview_valid_{channel}", "GET", f"/v1/preview/{channel}", {200 if previews_enabled else 503}))

    scenarios.extend(
        [
            Scenario("more_after_valid", "GET", "/v1/more/stranaua/after/229311", {200}),
            Scenario("more_before_valid", "GET", "/v1/more/stranaua/before/229311", {200}),
            Scenario("post_valid", "GET", "/v1/post/stranaua/229311", {200}),
            Scenario(
                "previews_valid",
                "POST",
                "/v1/previews",
                {200 if previews_enabled else 503},
                payload={"channels": ["durov", "stranaua", "nebudetafk"]},
            ),
            Scenario(
                "feed_valid",
                "POST",
                "/v1/feed",
                {200 if feed_enabled else 503},
                payload={"channels": ["durov", "stranaua", "nebudetafk"]},
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
    print("name|strict|method|path|status|ms|bytes|ok|preview")
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
                    result.body_preview,
                ]
            )
        )


def main() -> int:
    """Run full smoke matrix and return process exit code."""
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    exploratory_channels = [c.strip() for c in args.exploratory_channels.split(",") if c.strip()]

    detected_feed_enabled, detected_previews_enabled = detect_feature_state(base_url, args.timeout)

    if args.preset == "auto":
        expected_feed_enabled, expected_previews_enabled = detected_feed_enabled, detected_previews_enabled
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
    results = [call(base_url, scenario, args.timeout) for scenario in scenarios]
    print_results(results)

    strict_failures = [result for result in results if result.scenario.strict and not result.ok]
    exploratory_failures = [result for result in results if (not result.scenario.strict) and not result.ok]

    print("\nSUMMARY")
    print(f"  strict_total={len([r for r in results if r.scenario.strict])}")
    print(f"  strict_failures={len(strict_failures)}")
    print(f"  exploratory_total={len([r for r in results if not r.scenario.strict])}")
    print(f"  exploratory_failures={len(exploratory_failures)}")
    print(f"  detected_feed_enabled={detected_feed_enabled}")
    print(f"  detected_previews_enabled={detected_previews_enabled}")
    print(f"  expected_feed_enabled={expected_feed_enabled}")
    print(f"  expected_previews_enabled={expected_previews_enabled}")

    preset_mismatch = (
        args.preset != "auto"
        and (
            detected_feed_enabled != expected_feed_enabled
            or detected_previews_enabled != expected_previews_enabled
        )
    )
    if preset_mismatch:
        print("\nPRESET MISMATCH")
        print(
            "  Runtime feature state does not match selected preset. "
            "Check ENV flags used to start the server."
        )
        print(
            f"  expected: feed={expected_feed_enabled}, previews={expected_previews_enabled}"
        )
        print(
            f"  detected: feed={detected_feed_enabled}, previews={detected_previews_enabled}"
        )

    if strict_failures:
        print("\nSTRICT FAILURES")
        for result in strict_failures:
            print(
                f"- {result.scenario.name}: status={result.status}, "
                f"expected={sorted(result.scenario.expected_statuses)}, path={result.scenario.path}"
            )
        return 1

    if preset_mismatch:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
