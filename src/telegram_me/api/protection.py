"""Optional request protection: a per-client rate limit and proof of work.

Both are off by default and switched on by the server owner
(`ENABLE_RATE_LIMIT`, `ENABLE_POW`). The checks themselves are native
(`telegram_me_native.RateLimiter`, `ProofOfWork`) and take microseconds; they
run in one pure ASGI middleware before routing, so a rejected request never
reaches validation, the cache or t.me.

Order: the rate limit first (one map lookup), then proof of work. Rejected
proofs count against the rate limit, which bounds how fast anyone can probe.

Rate limit: GCRA per client IP in every process. With Redis and
`RATE_LIMIT_SHARED`, each process adds its accepted counts to a per-minute
counter in Redis once a second and blocks clients that went over the limit
there, so a client is limited across all workers and servers. The request
path never waits for Redis; the shared limit may be exceeded by up to one
second of traffic per process.

Proof of work: see `telegram_me_native` (`pow.rs`) for the scheme. Clients
get a challenge from `GET /v1/pow/challenge` (or from any rejection), reuse
it until it expires, and solve it once per request.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from telegram_me_native import ProofOfWork, RateLimiter

from telegram_me.cache import KEY_PREFIX, RedisCache
from telegram_me.settings import Settings

logger = logging.getLogger(__name__)

POW_HEADER = "X-PoW"
POW_CHALLENGE_PATH = "/v1/pow/challenge"
# Paths served without proof of work: docs, health checks, static files and
# the challenge itself. GraphiQL is exempt only as a page (GET without query).
_POW_EXEMPT = frozenset({"/", "/openapi.json", "/healthz", "/favicon.ico", POW_CHALLENGE_PATH})
_POW_EXEMPT_PREFIXES = ("/static/", "/docs/")
_POW_REJECTIONS: dict[str, tuple[int, str]] = {
    "missing": (428, "Proof of work required"),
    "expired": (428, "Proof-of-work challenge expired"),
    "too_easy": (428, "Proof-of-work challenge is easier than required"),
    "malformed": (400, "Malformed X-PoW header"),
    "forged": (403, "Proof-of-work challenge was not issued for this client"),
    "insufficient_work": (403, "Proof of work is insufficient"),
    "replayed": (403, "Proof of work was already used"),
}
# Headers browsers may read from cross-origin responses (CORS).
EXPOSED_HEADERS = [
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-PoW-Challenge",
    "X-PoW-Difficulty",
    "X-PoW-Expires",
]
# Shared rate limit: how often counts are pushed and the most clients per push.
_SYNC_INTERVAL = 1.0
_SYNC_MAX_CLIENTS = 2000


@dataclass(frozen=True, slots=True)
class Protection:
    """The enabled protections, built once per process from settings."""

    limiter: RateLimiter | None
    per_minute: int
    pow: ProofOfWork | None
    pow_ttl: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "Protection":
        """Build what `settings` enables."""
        limiter = None
        if settings.enable_rate_limit:
            burst = settings.rate_limit_burst or settings.rate_limit_rpm
            limiter = RateLimiter(settings.rate_limit_rpm, burst)
        pow_ = None
        if settings.enable_pow and settings.pow_secret is not None:
            pow_ = ProofOfWork(
                settings.pow_secret.get_secret_value().encode(),
                settings.pow_difficulty,
                settings.pow_challenge_ttl,
                bind_ip=settings.pow_bind_ip,
            )
        return cls(limiter, settings.rate_limit_rpm, pow_, settings.pow_challenge_ttl)

    @property
    def enabled(self) -> bool:
        """Whether any protection is on."""
        return self.limiter is not None or self.pow is not None


class ProtectionMiddleware:
    """Rate limit and proof of work, before anything else touches the request."""

    def __init__(self, app: ASGIApp, protection: Protection) -> None:
        self.app = app
        self._protection = protection

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: D102
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip: str = client[0] if client else ""
        extra: list[tuple[bytes, bytes]] = []

        limiter = self._protection.limiter
        if limiter is not None:
            allowed, remaining, retry_after = limiter.check(ip or "unknown")
            limit = str(self._protection.per_minute).encode()
            if not allowed:
                await _respond(
                    send,
                    429,
                    {"detail": "Rate limit exceeded"},
                    [
                        (b"retry-after", str(max(math.ceil(retry_after), 1)).encode()),
                        (b"x-ratelimit-limit", limit),
                        (b"x-ratelimit-remaining", b"0"),
                    ],
                )
                return
            extra += [
                (b"x-ratelimit-limit", limit),
                (b"x-ratelimit-remaining", str(remaining).encode()),
            ]

        pow_ = self._protection.pow
        if pow_ is not None and not _pow_exempt(scope):
            rejection = pow_.verify(_header(scope, b"x-pow"), scope["method"], _target(scope), ip)
            if rejection is not None:
                status, detail = _POW_REJECTIONS[rejection]
                challenge = issue_challenge(pow_, ip)
                await _respond(
                    send,
                    status,
                    {"detail": detail, "pow": {"code": rejection, **challenge}},
                    [
                        *extra,
                        (b"x-pow-challenge", challenge["challenge"].encode()),
                        (b"x-pow-difficulty", str(challenge["difficulty"]).encode()),
                        (b"x-pow-expires", str(challenge["expires"]).encode()),
                    ],
                )
                return

        if not extra:
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra:
                    headers[name.decode()] = value.decode()
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _pow_exempt(scope: Scope) -> bool:
    path: str = scope["path"]
    if path in _POW_EXEMPT or path.startswith(_POW_EXEMPT_PREFIXES):
        return True
    return path == "/graphql" and scope["method"] == "GET" and not scope.get("query_string")


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key == name:
            decoded: str = value.decode("latin-1")
            return decoded
    return None


def _target(scope: Scope) -> str:
    """The request target as the client sent it: raw path and query."""
    raw = scope.get("raw_path") or scope["path"].encode()
    query: bytes = scope.get("query_string") or b""
    target = raw + (b"?" + query if query else b"")
    decoded: str = target.decode("latin-1")
    return decoded


async def _respond(
    send: Send, status: int, body: dict[str, Any], headers: list[tuple[bytes, bytes]]
) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"cache-control", b"no-store"),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def issue_challenge(pow_: ProofOfWork, ip: str) -> dict[str, Any]:
    """A fresh challenge with everything a client needs to solve it."""
    challenge, bits, expires = pow_.issue(ip)
    return {
        "challenge": challenge,
        "difficulty": bits,
        "expires": expires,
        "algorithm": "sha256",
        "header": POW_HEADER,
        "input": "{challenge}\\n{nonce}\\n{METHOD} {target}",
    }


class PowChallenge(BaseModel):
    """A proof-of-work challenge and how to answer it."""

    challenge: str = Field(description="Send back unchanged as `<challenge>:<nonce>`.")
    difficulty: int = Field(description="Leading zero bits the SHA-256 hash must have.")
    expires: int = Field(description="Unix time after which the challenge is rejected.")
    algorithm: str = Field(description="Hash function: `sha256`.")
    header: str = Field(description="Request header that carries the answer: `X-PoW`.")
    input: str = Field(
        description=(
            "What is hashed, with `\\n` as newlines; `target` is the request path "
            "and query exactly as sent."
        )
    )


router = APIRouter(tags=["service"])


@router.get(
    POW_CHALLENGE_PATH,
    response_model=PowChallenge,
    summary="Get a proof-of-work challenge",
    description=(
        "Only when the server requires proof of work (`ENABLE_POW`). A challenge "
        "can be reused for any number of requests until it expires; each request "
        "needs its own nonce."
    ),
)
async def pow_challenge(request: Request) -> dict[str, Any]:  # noqa: D103
    protection: Protection = request.app.state.protection
    if protection.pow is None:  # pragma: no cover - the route is only added when enabled
        msg = "proof of work is disabled"
        raise RuntimeError(msg)
    client = request.client
    return issue_challenge(protection.pow, client.host if client else "")


@asynccontextmanager
async def sharing_rate_limits(
    limiter: RateLimiter, shared: RedisCache, per_minute: int
) -> AsyncIterator[None]:
    """Count each client across processes in Redis while the block runs."""

    async def loop() -> None:
        while True:
            await asyncio.sleep(_SYNC_INTERVAL)
            await sync_rate_limits(limiter, shared, per_minute)

    task = asyncio.create_task(loop(), name="rate-limit-sync")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def sync_rate_limits(
    limiter: RateLimiter, shared: RedisCache, per_minute: int, now: float | None = None
) -> int:
    """Push this process's counts and block clients over the shared limit.

    Uses one counter per client and minute in Redis. Returns how many clients
    were blocked.
    """
    counts = limiter.take_counts()
    if not counts or not shared.available:
        return 0
    if len(counts) > _SYNC_MAX_CLIENTS:
        counts = sorted(counts, key=lambda item: item[1], reverse=True)[:_SYNC_MAX_CLIENTS]
    now = time.time() if now is None else now
    window = int(now // 60)
    until_next_window = 60 - now % 60
    limit = max(per_minute, limiter.burst)
    try:
        async with shared.client.pipeline(transaction=False) as pipe:
            for client, count in counts:
                key = f"{KEY_PREFIX}rl:{window}:{client}"
                pipe.incrby(key, count)
                pipe.expire(key, 120)
            results = await pipe.execute()
    except (RedisError, OSError) as exc:
        shared.trip(exc)
        return 0
    blocked = 0
    for (client, _), total in zip(counts, results[::2], strict=True):
        if int(total) > limit:
            limiter.block(client, until_next_window)
            blocked += 1
    return blocked
