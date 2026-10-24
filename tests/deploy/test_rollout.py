"""deploy/rollout.sh, the fleet logic of the deploy workflow, with stub ssh/scp.

The stubs record every remote command and fail the ones a test asks for, so
the order of deploys, the canary check and the fleet rollback are checked
without servers. What remote-deploy.sh does on a server is tested with real
Docker in test_remote_deploy.py.
"""

import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "docker.io/koval01/telegram-me:sha-0123456789ab"
SERVERS = "203.0.113.1 203.0.113.2"

# ssh/scp stand-in: logs "<host> <remote command>" and exits 1 when
# "<host>:<kind>" (kind = deploy, verify or rollback) is in $STUB_FAIL.
STUB = """#!{python}
import os, sys
args = sys.argv[1:]
with_value = {{"-i", "-o", "-p", "-P", "-F"}}
words = []
skip = False
for arg in args:
    if skip:
        skip = False
    elif arg in with_value:
        skip = True
    elif not arg.startswith("-"):
        words.append(arg)
name = os.path.basename(sys.argv[0])
if name == "scp":
    sys.exit(0)
host = words[0].split("@")[-1]
command = " ".join(words[1:])
if "remote-deploy.sh" not in command:
    sys.exit(0)
kind = "verify" if "--verify" in command else "rollback" if "--rollback" in command else "deploy"
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(f"{{host}} {{kind}} {{command}}\\n")
sys.exit(1 if f"{{host}}:{{kind}}" in os.environ.get("STUB_FAIL", "").split() else 0)
"""


def _bash_is_recent() -> bool:
    bash = shutil.which("bash")
    if bash is None:
        return False
    major = subprocess.run(  # noqa: S603 - fixed command line
        [bash, "-c", "echo ${BASH_VERSINFO[0]}${BASH_VERSINFO[1]}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return major.isdigit() and int(major) >= 44


pytestmark = pytest.mark.skipif(not _bash_is_recent(), reason="needs bash >= 4.4")


@dataclass
class Rollout:
    returncode: int
    output: str
    calls: list[tuple[str, str]]
    commands: list[str]


type Run = Callable[..., Rollout]


@pytest.fixture
def run(tmp_path: Path) -> Run:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ssh", "scp"):
        stub = bin_dir / name
        stub.write_text(STUB.format(python=sys.executable))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"

    def rollout(fail: str = "", **env: str) -> Rollout:
        log.write_text("")
        result = subprocess.run(  # noqa: S603 - fixed command line
            ["bash", str(ROOT / "deploy" / "rollout.sh")],  # noqa: S607
            cwd=ROOT,
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(tmp_path),
                "IMAGE": IMAGE,
                "SERVERS": SERVERS,
                "SSH_USER": "ubuntu",
                "CANARY_SOAK": "1",
                "SMOKE_RETRY_DELAY": "0",
                "STUB_LOG": str(log),
                "STUB_FAIL": fail,
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        lines = [line.split(" ", 2) for line in log.read_text().splitlines()]
        return Rollout(
            result.returncode,
            result.stdout + result.stderr,
            [(host.rsplit(".", 1)[1], kind) for host, kind, _ in lines],
            [command for _, _, command in lines],
        )

    return rollout


def test_canary_first_then_the_rest(run: Run) -> None:
    result = run()
    assert result.returncode == 0, result.output
    assert result.calls == [("1", "deploy"), ("1", "verify"), ("2", "deploy")]
    assert all("SMOKE='1'" in command for command in result.commands)
    assert f"remote-deploy.sh '{IMAGE}'" in result.commands[0]


def test_failure_on_a_later_server_rolls_back_the_earlier_ones(run: Run) -> None:
    result = run(fail="203.0.113.2:deploy")
    assert result.returncode == 1
    # Server 2 rolled itself back (remote-deploy.sh); server 1 is put back too.
    assert result.calls == [("1", "deploy"), ("1", "verify"), ("2", "deploy"), ("1", "rollback")]
    assert "SMOKE='0'" in result.commands[-1]
    assert "server 2: " in result.output


def test_failed_canary_stops_before_other_servers(run: Run) -> None:
    result = run(fail="203.0.113.1:verify")
    assert result.returncode == 1
    assert result.calls == [("1", "deploy"), ("1", "verify"), ("1", "rollback")]
    assert "canary" in result.output


def test_failure_on_the_first_server_touches_nothing_else(run: Run) -> None:
    result = run(fail="203.0.113.1:deploy")
    assert result.returncode == 1
    assert result.calls == [("1", "deploy")]


def test_failed_public_smoke_test_rolls_back_every_server(run: Run) -> None:
    # Nothing listens there: the smoke matrix fails at once.
    result = run(PUBLIC_URL="http://127.0.0.1:9")
    assert result.returncode == 1
    assert result.calls[-2:] == [("2", "rollback"), ("1", "rollback")]
    assert "public smoke test failed" in result.output


def test_failed_rollback_is_reported(run: Run) -> None:
    result = run(fail="203.0.113.2:deploy 203.0.113.1:rollback")
    assert result.returncode == 1
    assert "server 1: rollback failed" in result.output


def test_manual_run_without_smoke_tests(run: Run) -> None:
    result = run(SMOKE="0", PUBLIC_URL="http://127.0.0.1:9")
    assert result.returncode == 0, result.output
    assert all("SMOKE='0'" in command for command in result.commands)


def test_single_server_has_no_canary_soak(run: Run) -> None:
    result = run(SERVERS="203.0.113.1")
    assert result.returncode == 0, result.output
    assert result.calls == [("1", "deploy")]


@pytest.mark.parametrize(
    "env",
    [
        {"IMAGE": "image; rm -rf ~"},
        {"COMPOSE_PROFILES": "proxy'"},
        {"CANARY_SOAK": "1m"},
        {"SMOKE": "yes"},
    ],
)
def test_unsafe_values_are_rejected_before_any_server(run: Run, env: dict[str, str]) -> None:
    result = run(**env)
    assert result.returncode == 1
    assert result.calls == []
