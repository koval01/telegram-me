#!/usr/bin/env python3
"""Measure what t.me sees of each HTTP client: TLS, HTTP/2 and header fingerprints.

Sends a navigation (GET) and the "load more" XHR (POST) through the production
fetchers (`ReqwestFetcher`, `BrowserFetcher`) to a TLS echo service and prints
JA3, JA4, the Akamai HTTP/2 fingerprint and the header order of each:

    uv run python scripts/fingerprint.py
    uv run python scripts/fingerprint.py --runs 5 --json > fingerprints.json

The echo service must answer like https://tls.peet.ws/api/all. JA3 hashes of
Chrome change on every connection (it permutes its TLS extensions since
Chrome 110), so they are counted, not compared; JA4 sorts them and is stable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any
from urllib.parse import urlsplit

from telegram_me.telegram.client import (
    DEFAULT_BROWSER_PLATFORM,
    DEFAULT_BROWSER_PROFILE,
    BrowserFetcher,
    Fetcher,
    ReqwestFetcher,
)

CLIENTS: dict[str, type[ReqwestFetcher | BrowserFetcher]] = {
    "reqwest": ReqwestFetcher,
    "browser": BrowserFetcher,
}


async def _probe(fetcher: Fetcher, path: str, *, xhr: bool) -> dict[str, Any]:
    if xhr:
        result = await fetcher.fetch(
            "POST", path, query={"before": 10}, headers={"x-requested-with": "XMLHttpRequest"}
        )
    else:
        result = await fetcher.fetch("GET", path)
    if result.status != 200:
        msg = f"echo service answered {result.status}"
        raise RuntimeError(msg)
    echo: dict[str, Any] = json.loads(result.text)
    return echo


def _summary(echoes: list[dict[str, Any]]) -> dict[str, Any]:
    first = echoes[0]
    http2 = first.get("http2") or {}
    frames = http2.get("sent_frames") or []
    headers = next((f["headers"] for f in frames if f.get("frame_type") == "HEADERS"), [])
    return {
        "http_version": first.get("http_version"),
        "user_agent": first.get("user_agent"),
        "ja3_hashes": sorted({echo["tls"]["ja3_hash"] for echo in echoes}),
        "ja4": sorted({echo["tls"]["ja4"] for echo in echoes}),
        "peetprint_hash": sorted({echo["tls"]["peetprint_hash"] for echo in echoes}),
        "akamai": http2.get("akamai_fingerprint"),
        "akamai_hash": http2.get("akamai_fingerprint_hash"),
        "headers": [
            line.split(":", 2)[1] if line.startswith(":") else line.split(":")[0]
            for line in headers
        ],
    }


async def measure(url: str, runs: int, profile: str, platform: str) -> dict[str, Any]:
    """Fingerprints per client and request kind."""
    parts = urlsplit(url)
    base, path = f"{parts.scheme}://{parts.netloc}", parts.path or "/"
    report: dict[str, Any] = {"url": url, "profile": profile, "platform": platform}
    for name, factory in CLIENTS.items():
        report[name] = {}
        for kind, xhr in (("navigation", False), ("xhr", True)):
            echoes = []
            # A new client per run: a new connection and a full handshake.
            for _ in range(runs):
                fetcher = factory(15, base, profile=profile, platform=platform)
                try:
                    echoes.append(await _probe(fetcher, path, xhr=xhr))
                finally:
                    await fetcher.close()
            report[name][kind] = _summary(echoes)
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"Profile `{report['profile']}` on `{report['platform']}`, echo: {report['url']}",
        "",
        "| Client | Request | JA4 | JA3 (distinct/runs) | Akamai HTTP/2 | Akamai hash |",
        "|---|---|---|---|---|---|",
    ]
    for name in CLIENTS:
        for kind, row in report[name].items():
            lines.append(
                f"| {name} | {kind} | `{', '.join(row['ja4'])}` "
                f"| {len(row['ja3_hashes'])}/{report['runs']} | `{row['akamai']}` "
                f"| `{row['akamai_hash']}` |"
            )
    lines.append("")
    for name in CLIENTS:
        for kind, row in report[name].items():
            lines.append(
                f"- **{name} {kind}** ({row['http_version']}): {', '.join(row['headers'])}"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="https://tls.peet.ws/api/all")
    parser.add_argument("--runs", type=int, default=3, help="connections per client and kind")
    parser.add_argument("--profile", default=DEFAULT_BROWSER_PROFILE)
    parser.add_argument("--platform", default=DEFAULT_BROWSER_PLATFORM)
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args()
    report = asyncio.run(measure(args.url, args.runs, args.profile, args.platform))
    report["runs"] = args.runs
    print(json.dumps(report, indent=2) if args.json else _markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
