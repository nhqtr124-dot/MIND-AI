"""Real Docker tests (skipped when no Docker daemon is reachable)."""

import urllib.request

import pytest

from mind_sandbox import PROJECT_TEMPLATES, Limits, SandboxError, docker_available, materialize, run, start_service, stop, wait_until_ready

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")


def test_runs_code_and_captures_output() -> None:
    wd = materialize({"main.py": "import sys\nprint('hello from sandbox')\nprint('err', file=sys.stderr)\nsys.exit(3)\n"})
    r = run(wd, ["python", "main.py"])
    assert r.exit_code == 3 and "hello from sandbox" in r.stdout and "err" in r.stderr


def test_no_network() -> None:
    code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 80), timeout=3)\n    print('CONNECTED')\nexcept OSError as e:\n    print('BLOCKED', e)\n"
    r = run(materialize({"n.py": code}), ["python", "n.py"])
    assert "BLOCKED" in r.stdout and "CONNECTED" not in r.stdout


def test_timeout_kills() -> None:
    r = run(materialize({"s.py": "import time\ntime.sleep(60)\n"}), ["python", "s.py"], limits=Limits(timeout_s=2))
    assert r.timed_out and not r.ok and r.duration_s < 30


def test_memory_limit() -> None:
    r = run(materialize({"m.py": "x = bytearray(400 * 1024 * 1024)\nprint('allocated')\n"}), ["python", "m.py"], limits=Limits(memory_mb=128))
    assert not r.ok and "allocated" not in r.stdout


def test_non_root_and_read_only_root() -> None:
    code = "import os\nprint('uid', os.getuid())\ntry:\n    open('/etc/x', 'w')\n    print('WROTE')\nexcept OSError:\n    print('RO')\n"
    r = run(materialize({"u.py": code}), ["python", "u.py"])
    assert "uid 10001" in r.stdout and "RO" in r.stdout


def test_path_escape_rejected() -> None:
    with pytest.raises(SandboxError):
        materialize({"../escape.txt": "x"})


@pytest.mark.parametrize("key", list(PROJECT_TEMPLATES))
def test_templates_pass_their_tests_and_serve_preview(key: str) -> None:
    t = PROJECT_TEMPLATES[key]
    wd = materialize(t.files)
    r = run(wd, list(t.test_command), runtime=t.runtime, limits=Limits(timeout_s=60))
    assert r.ok, r.stdout + r.stderr
    h = start_service(wd, list(t.preview_command), t.port, runtime=t.runtime)
    try:
        assert wait_until_ready(h), "preview did not become ready"
        with urllib.request.urlopen(h.url + "/", timeout=5) as resp:
            assert resp.status == 200 and b"<html" in resp.read().lower()
    finally:
        stop(h.container_id)
