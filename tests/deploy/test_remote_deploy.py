"""deploy/remote-deploy.sh on a real Docker host, as on a production server.

The production stack (deploy/compose.prod.yaml) runs the image under test
(`TME_TEST_IMAGE`) against a Valkey and the fake t.me of the integration
tests, both reached through the stack's network gateway like the external
Redis of production. Two images derived from it stand for broken releases:
one never becomes healthy, the other is healthy but fails every page request,
which only the smoke test notices. `TME_PREVIOUS_IMAGE` (in CI the image
production runs now) is the release the upgrade starts from.

Needs Docker, ports 443, 16390, 18090 and 18100, and no other `telegram-me`
compose project on the host (the stack has a fixed name and subnet).
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("TME_TEST_IMAGE", "")
PREVIOUS = os.environ.get("TME_PREVIOUS_IMAGE", "") or IMAGE
UNHEALTHY = "telegram-me-test:unhealthy"
BROKEN = "telegram-me-test:broken"
# The gateway of the stack's network (compose.prod.yaml) is this host.
GATEWAY = "172.29.53.1"
APP_PORT = "18100"

pytestmark = [
    pytest.mark.deploy,
    pytest.mark.skipif(not IMAGE, reason="set TME_TEST_IMAGE to the image to test"),
]

# Every page request fails, /healthz keeps working: a release that is
# "healthy" but broken.
BROKEN_SITECUSTOMIZE = """
import telegram_me_native

def _broken(*args, **kwargs):
    raise RuntimeError("deliberately broken release")

for _name in ("parse_channel_page", "parse_more_page", "parse_preview_page"):
    setattr(telegram_me_native, _name, _broken)
"""


def docker(*args: str, check: bool = True, **kwargs: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed command line
        ["docker", *args],  # noqa: S607
        check=check,
        capture_output=True,
        text=True,
        input=kwargs.get("input"),
    )


@dataclass
class Server:
    """An application directory as rollout.sh leaves it on a server."""

    app_dir: Path

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run remote-deploy.sh with `args`."""
        result = subprocess.run(  # noqa: S603 - fixed command line
            ["bash", str(self.app_dir / "remote-deploy.sh"), *args],  # noqa: S607
            env={
                **os.environ,
                "APP_DIR": str(self.app_dir),
                "APP_PORT": APP_PORT,
                "SKIP_PULL": "1",
                "WAIT_TIMEOUT": "60",
                "SMOKE_RETRY_DELAY": "0",
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        print(result.stdout, result.stderr)  # noqa: T201 - shown when a test fails
        return result

    def deploy(self, image: str, **env: str) -> subprocess.CompletedProcess[str]:
        return self.run(image, **env)

    def state(self, name: str = ".deployed-image") -> str | None:
        path = self.app_dir / name
        return path.read_text().strip() if path.exists() else None

    def running_image(self) -> str:
        container = docker(
            "compose", "-f", str(self.app_dir / "compose.prod.yaml"), "ps", "-q", "api"
        ).stdout.strip()
        return docker("inspect", "-f", "{{.Config.Image}}", container).stdout.strip()

    def curl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed command line
            ["curl", "-fsS", "--max-time", "20", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )


def _write_env(app_dir: Path, *, proxy: bool) -> None:
    lines = [
        f"REDIS_URL=redis://{GATEWAY}:16390/0",
        f"TELEGRAM_BASE_URL=http://{GATEWAY}:18090",
        "ENABLE_FEED=true",
        "FEED_REFRESH_MODE=off",
        "CACHE_MODE=aggressive",
        "LOG_FORMAT=json",
        f"APP_PORT={APP_PORT}",
        f"APP_BIND={'127.0.0.1' if proxy else '0.0.0.0'}",  # noqa: S104 - as in production
        f"FORWARDED_ALLOW_IPS={'172.29.53.0/24' if proxy else '127.0.0.1'}",
        "PUBLIC_HOST=api.test",
        "CADDY_TLS=internal",
    ]
    (app_dir / ".env").write_text("\n".join(lines) + "\n")
    (app_dir / ".env").chmod(0o600)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    app_dir = tmp_path_factory.mktemp("server") / "telegram-me"
    app_dir.mkdir()
    for source in ("deploy/compose.prod.yaml", "deploy/remote-deploy.sh", "deploy/Caddyfile"):
        shutil.copy(ROOT / source, app_dir)
    shutil.copy(ROOT / "scripts" / "smoke_matrix.py", app_dir)
    (app_dir / "certs").mkdir()
    _write_env(app_dir, proxy=False)

    if PREVIOUS != IMAGE:
        docker("pull", "--quiet", PREVIOUS)
    docker(
        "build", "-t", UNHEALTHY, "-", input=f"FROM {IMAGE}\nHEALTHCHECK --interval=1s CMD false\n"
    )
    context = tmp_path_factory.mktemp("broken")
    (context / "sitecustomize.py").write_text(BROKEN_SITECUSTOMIZE)
    (context / "Dockerfile").write_text(
        f"FROM {IMAGE}\nCOPY sitecustomize.py /broken/\nENV PYTHONPATH=/broken\n"
    )
    docker("build", "-t", BROKEN, str(context))
    helpers = {
        "tme-deploy-valkey": ["-p", "16390:6379", "valkey/valkey:9.1-alpine"],
        "tme-deploy-upstream": [
            "-p",
            "18090:8080",
            "-e",
            "PORT=8080",
            "-v",
            f"{ROOT / 'tests'}:/tests:ro",
            "--entrypoint",
            "python",
            IMAGE,
            "/tests/integration/fake_telegram.py",
        ],
    }
    for name, args in helpers.items():
        docker("rm", "-f", name, check=False)
        docker("run", "-d", "--name", name, *args)
    try:
        yield Server(app_dir)
    finally:
        compose = ["compose", "-f", str(app_dir / "compose.prod.yaml"), "--profile", "proxy"]
        subprocess.run(  # noqa: S603 - fixed command line
            ["docker", *compose, "down", "-v", "--remove-orphans"],  # noqa: S607
            env={**os.environ, "APP_IMAGE": IMAGE},
            capture_output=True,
            check=False,
        )
        for name in helpers:
            docker("rm", "-f", name, check=False)
        docker("rmi", UNHEALTHY, BROKEN, check=False)


def ensure(server: Server, image: str) -> None:
    """Deploy `image` unless it already runs."""
    if server.state() != image:
        assert server.deploy(image, SMOKE="0").returncode == 0


def test_upgrade_from_the_previous_release(server: Server) -> None:
    # The previous release may not know TELEGRAM_BASE_URL: no smoke test for it.
    ensure(server, PREVIOUS)

    result = server.deploy(IMAGE)
    assert result.returncode == 0
    assert "smoke test passed" in result.stdout
    assert server.state() == IMAGE
    assert server.state(".previous-image") == (PREVIOUS if PREVIOUS != IMAGE else None)
    assert server.running_image() == IMAGE
    assert f"APP_IMAGE={IMAGE}" in (server.app_dir / ".env").read_text()
    assert server.curl(f"http://127.0.0.1:{APP_PORT}/v1/body/durov").returncode == 0


def test_unhealthy_release_is_rolled_back(server: Server) -> None:
    ensure(server, IMAGE)
    result = server.deploy(UNHEALTHY, WAIT_TIMEOUT="20")
    assert result.returncode == 1
    assert "did not become healthy" in result.stdout
    assert f"rolling back to {IMAGE}" in result.stdout
    assert server.state() == IMAGE
    assert server.running_image() == IMAGE
    assert server.curl(f"http://127.0.0.1:{APP_PORT}/healthz").returncode == 0


def test_healthy_release_failing_the_smoke_test_is_rolled_back(server: Server) -> None:
    ensure(server, IMAGE)
    result = server.deploy(BROKEN)
    assert result.returncode == 1
    assert "failed the smoke test" in result.stdout
    assert "STRICT FAILURES" in result.stdout
    assert server.state() == IMAGE
    assert server.running_image() == IMAGE
    assert server.curl(f"http://127.0.0.1:{APP_PORT}/v1/body/durov").returncode == 0


def test_verify_checks_the_running_image(server: Server) -> None:
    ensure(server, IMAGE)
    assert server.run("--verify", IMAGE).returncode == 0
    result = server.run("--verify", BROKEN)
    assert result.returncode == 1
    assert f"runs {IMAGE} instead of {BROKEN}" in result.stdout


def test_rollback_returns_to_the_previous_release(server: Server) -> None:
    if PREVIOUS == IMAGE:
        pytest.skip("set TME_PREVIOUS_IMAGE to test a rollback to another release")
    ensure(server, PREVIOUS)
    assert server.deploy(IMAGE).returncode == 0

    result = server.run("--rollback")
    assert result.returncode == 0
    assert server.state() == PREVIOUS
    assert server.state(".previous-image") is None
    assert server.running_image() == PREVIOUS
    # A second rollback must not flip back to the release just undone.
    second = server.run("--rollback")
    assert second.returncode == 1
    assert "nothing to roll back to" in second.stdout


def test_proxy_profile_serves_through_caddy(server: Server) -> None:
    _write_env(server.app_dir, proxy=True)
    try:
        result = server.deploy(IMAGE, COMPOSE_PROFILES="proxy")
        assert result.returncode == 0
        resolve = ["--resolve", "api.test:443:127.0.0.1", "--insecure"]
        assert server.curl(*resolve, "https://api.test/healthz").returncode == 0
        page = server.curl(*resolve, "-D", "-", "https://api.test/v1/body/durov")
        assert page.returncode == 0, page.stderr
        assert "x-app-node" in page.stdout.lower()
        assert "COMPOSE_PROFILES=proxy" in (server.app_dir / ".env").read_text()
    finally:
        _write_env(server.app_dir, proxy=False)
