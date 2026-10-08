"""Docker-backed sandbox.

Untrusted (including AI-generated) code never runs on the API host. Each run gets:
  * a throwaway container from a pinned runtime image, running as a non-root user
  * no network (``--network none``) for one-off runs; previews join an
    ``--internal`` bridge network that has no route to the internet
  * memory, CPU, PID and wall-clock limits, all capabilities dropped,
    ``no-new-privileges`` and a read-only root filesystem
  * a copy of the project files, never a mount of the server's own directories
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PREVIEW_NETWORK = os.environ.get("MIND_SANDBOX_NETWORK", "mind-sandbox-net")
LABEL = "mind.sandbox=1"

RUNTIMES: dict[str, str] = {
    "python": os.environ.get("MIND_SANDBOX_PYTHON_IMAGE", "mind-sandbox-python:latest"),
    "python-slim": "python:3.12-slim",
    "node": os.environ.get("MIND_SANDBOX_NODE_IMAGE", "node:22-alpine"),
}

MAX_OUTPUT = 64_000


class SandboxError(RuntimeError):
    pass


@dataclass
class Limits:
    timeout_s: float = 30.0
    memory_mb: int = 512
    cpus: float = 1.0
    pids: int = 256
    tmpfs_mb: int = 64


@dataclass
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    oom_killed: bool = False
    truncated: bool = False
    image: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class ServiceHandle:
    container_id: str
    name: str
    ip: str
    port: int
    workdir: str
    image: str
    started_at: float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{self.port}"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def image_present(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False).returncode == 0


def ensure_network() -> None:
    probe = subprocess.run(["docker", "network", "inspect", PREVIEW_NETWORK], capture_output=True, check=False)
    if probe.returncode != 0:
        subprocess.run(["docker", "network", "create", "--internal", "--label", LABEL, PREVIEW_NETWORK], capture_output=True, check=True)


def resolve_image(runtime: str) -> str:
    image = RUNTIMES.get(runtime)
    if image is None:
        raise SandboxError(f"unknown runtime '{runtime}' (available: {', '.join(RUNTIMES)})")
    if not image_present(image):
        # Fall back from the prebuilt python image to the stock one if it has not been built yet.
        if runtime == "python" and image_present(RUNTIMES["python-slim"]):
            return RUNTIMES["python-slim"]
        raise SandboxError(f"runtime image '{image}' is not available; run scripts/setup-sandbox.sh")
    return image


def _security_flags(limits: Limits) -> list[str]:
    return [
        "--memory", f"{limits.memory_mb}m",
        "--memory-swap", f"{limits.memory_mb}m",
        "--cpus", str(limits.cpus),
        "--pids-limit", str(limits.pids),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,size={limits.tmpfs_mb}m",
        "--user", "10001:10001",
        "--label", LABEL,
    ]  # fmt: skip


def materialize(files: Mapping[str, bytes | str], root: Path | None = None) -> Path:
    """Write project files to a fresh temp directory the sandbox user can write to."""
    base = Path(tempfile.mkdtemp(prefix="mind-sbx-", dir=root))
    for rel, content in files.items():
        p = (base / rel).resolve()
        if base.resolve() not in p.parents:
            raise SandboxError(f"path escapes workspace: {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str) else content)
    for dirpath, dirnames, filenames in os.walk(base):
        os.chmod(dirpath, 0o777)  # noqa: S103 - throwaway dir; container runs as uid 10001
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o666)  # noqa: S103
    return base


def _trim(b: bytes) -> tuple[str, bool]:
    if len(b) > MAX_OUTPUT:
        return b[:MAX_OUTPUT].decode(errors="replace") + "\n... [output truncated]", True
    return b.decode(errors="replace"), False


def run(
    workdir: Path,
    command: list[str],
    runtime: str = "python",
    limits: Limits | None = None,
    env: Mapping[str, str] | None = None,
    network: Literal["none", "egress"] = "none",
) -> RunResult:
    """Run a command to completion. ``network='egress'`` is only for dependency installs."""
    limits = limits or Limits()
    image = resolve_image(runtime)
    name = f"mind-run-{uuid.uuid4().hex[:12]}"
    args = ["docker", "run", "--rm", "--name", name, *_security_flags(limits)]
    args += ["--network", "none" if network == "none" else "bridge"]
    args += ["-v", f"{workdir}:/workspace:rw", "-w", "/workspace", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    args += [image, *command]
    start = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=limits.timeout_s + 5)  # +5 s for container start-up
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        out, err = proc.communicate(timeout=30)
    duration = time.monotonic() - start
    so, t1 = _trim(out)
    se, t2 = _trim(err)
    code = proc.returncode
    return RunResult(
        exit_code=None if timed_out else code,
        stdout=so,
        stderr=se + ("\n[killed: wall-clock limit exceeded]" if timed_out else ""),
        duration_s=round(duration, 3),
        timed_out=timed_out,
        oom_killed=code == 137 and not timed_out,
        truncated=t1 or t2,
        image=image,
    )


def start_service(workdir: Path, command: list[str], port: int, runtime: str = "python", limits: Limits | None = None) -> ServiceHandle:
    """Start a long-running preview server on the internal (no-internet) network."""
    limits = limits or Limits(memory_mb=512, cpus=1.0)
    image = resolve_image(runtime)
    ensure_network()
    name = f"mind-preview-{uuid.uuid4().hex[:12]}"
    args = ["docker", "run", "-d", "--name", name, *_security_flags(limits), "--network", PREVIEW_NETWORK]
    args += ["-v", f"{workdir}:/workspace:rw", "-w", "/workspace", "-e", "HOME=/tmp", "-e", f"PORT={port}", image, *command]
    proc = subprocess.run(args, capture_output=True, check=False)
    if proc.returncode != 0:
        raise SandboxError(f"failed to start preview: {proc.stderr.decode(errors='replace')[-500:]}")
    cid = proc.stdout.decode().strip()
    ip = subprocess.run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid], capture_output=True, check=True
    ).stdout.decode().strip()
    return ServiceHandle(cid, name, ip, port, str(workdir), image)


def wait_until_ready(handle: ServiceHandle, timeout_s: float = 20.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_running(handle.container_id):
            return False
        try:
            with urllib.request.urlopen(handle.url + "/", timeout=2) as r:  # noqa: S310 - internal container URL
                return r.status < 500
        except urllib.error.HTTPError as e:
            return e.code < 500
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def is_running(container_id: str) -> bool:
    p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container_id], capture_output=True, check=False)
    return p.returncode == 0 and p.stdout.decode().strip() == "true"


def logs(container_id: str, tail: int = 200) -> str:
    p = subprocess.run(["docker", "logs", "--tail", str(tail), container_id], capture_output=True, check=False)
    return _trim(p.stdout + p.stderr)[0]


def stop(container_id: str) -> None:
    subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)


def cleanup_stale(max_age_s: float = 3600) -> int:
    """Remove sandbox containers older than max_age_s. Returns the number removed."""
    from datetime import datetime

    ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL}"], capture_output=True, check=False).stdout.decode().split()
    removed = 0
    for cid in ids:
        created = subprocess.run(["docker", "inspect", "-f", "{{.Created}}", cid], capture_output=True, check=False).stdout.decode().strip()
        try:
            # Docker reports RFC 3339 with nanoseconds; seconds precision is enough here.
            ts = datetime.fromisoformat(created[:19] + "+00:00").timestamp()
        except ValueError:
            continue
        if time.time() - ts > max_age_s:
            stop(cid)
            removed += 1
    return removed
