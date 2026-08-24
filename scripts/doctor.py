#!/usr/bin/env python3
"""``make doctor`` — first-run environment check (§19).

Answers the question a new operator actually has: *will `make demo` work on this
machine, and if not, what exactly is wrong?* Every check reports a verdict and,
when it fails, the specific thing to change. Nothing here mutates the machine.

Exit code 0 means the demo can run; 1 means at least one blocking check failed.
Warnings never fail the run — they are things that will be slow or degraded, not
things that will not work.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Ports the demo and dev stacks bind. A port in use is the single most common
#: reason a first run fails, and the least self-explanatory.
PORTS = {
    8080: "demo app (docker-compose.demo.yml)",
    8000: "API (make dev)",
    5173: "Vite dev server (make dev)",
    5432: "Postgres (make dev / make infra)",
    6379: "Redis (make dev / make infra)",
    9000: "MinIO (make dev / make infra)",
}

#: Building the SPA and generating 120 days of fixtures in one container is the
#: memory high-water mark of `make demo`.
MIN_DOCKER_MEMORY_GB = 4.0
MIN_FREE_DISK_GB = 5.0


class Verdict(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class Check:
    name: str
    verdict: Verdict
    detail: str
    remedy: str = ""


def _run(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


def check_python() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 12):
        return Check(
            "python",
            Verdict.FAIL,
            f"Python {major}.{minor} on PATH",
            "The workspace requires Python 3.12+ (see .python-version); uv installs it.",
        )
    return Check("python", Verdict.OK, f"Python {major}.{minor}")


def check_tool(binary: str, *, required: bool, purpose: str, install: str) -> Check:
    path = shutil.which(binary)
    if path:
        code, out = _run([binary, "--version"])
        version = out.splitlines()[0] if code == 0 and out else "present"
        return Check(binary, Verdict.OK, version)
    return Check(
        binary,
        Verdict.FAIL if required else Verdict.WARN,
        f"not on PATH — needed for {purpose}",
        install,
    )


def check_docker_daemon() -> Iterator[Check]:
    if shutil.which("docker") is None:
        yield Check(
            "docker",
            Verdict.WARN,
            "not on PATH",
            "Install Docker Desktop / Engine for `make demo`, or run `make demo-native`.",
        )
        return

    code, out = _run(["docker", "info", "--format", "{{json .}}"])
    if code != 0:
        yield Check(
            "docker daemon",
            Verdict.WARN,
            "not reachable",
            "Start Docker, or run `make demo-native` (no Docker required).",
        )
        return

    yield Check("docker daemon", Verdict.OK, "reachable")
    try:
        info = json.loads(out)
    except json.JSONDecodeError:
        return

    total = info.get("MemTotal")
    if isinstance(total, int) and total > 0:
        gb = total / (1024**3)
        if gb < MIN_DOCKER_MEMORY_GB:
            yield Check(
                "docker memory",
                Verdict.WARN,
                f"{gb:.1f} GB available to the daemon",
                f"Raise Docker's memory limit to at least {MIN_DOCKER_MEMORY_GB:.0f} GB; "
                "the SPA build is the peak.",
            )
        else:
            yield Check("docker memory", Verdict.OK, f"{gb:.1f} GB")

    if not info.get("ServerVersion"):
        return
    yield Check("docker version", Verdict.OK, str(info["ServerVersion"]))


def check_port(port: int, purpose: str) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        in_use = probe.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        remedy = f"Stop whatever holds {port} before starting the {purpose.split(' (')[0]}."
        if port == 8080:
            remedy += " Or publish the demo elsewhere: SNOWOBS_DEMO_PORT=8081 make demo."
        return Check(f"port {port}", Verdict.WARN, f"already in use ({purpose})", remedy)
    return Check(f"port {port}", Verdict.OK, f"free ({purpose})")


def check_disk() -> Check:
    usage = shutil.disk_usage(REPO_ROOT)
    free_gb = usage.free / (1024**3)
    if free_gb < MIN_FREE_DISK_GB:
        return Check(
            "disk",
            Verdict.WARN,
            f"{free_gb:.1f} GB free at {REPO_ROOT}",
            f"The images, the venv, and the fixture lake want ~{MIN_FREE_DISK_GB:.0f} GB.",
        )
    return Check("disk", Verdict.OK, f"{free_gb:.1f} GB free")


def check_settings() -> Check:
    """Load the real settings object — a bad .env should fail here, not at boot."""
    try:
        sys.path.insert(0, str(REPO_ROOT / "packages" / "common" / "src"))
        from snowobs_common.config import load_settings
    except ImportError as exc:
        return Check(
            "configuration",
            Verdict.WARN,
            f"settings module not importable ({exc})",
            "Run `uv sync --all-packages --dev` first.",
        )

    try:
        settings = load_settings()
    except Exception as exc:  # the settings loader raises ConfigurationError
        return Check(
            "configuration",
            Verdict.FAIL,
            str(exc),
            "Fix the offending value in .env (see .env.example for every setting).",
        )

    detail = (
        f"mode={settings.mode} tenancy={settings.tenancy} "
        f"llm={settings.llm.provider} storage={settings.storage.provider}"
    )
    return Check("configuration", Verdict.OK, detail)


def check_fixture_lake() -> Check:
    lake = REPO_ROOT / ".data" / "default"
    landed = (
        [d for d in lake.iterdir() if d.is_dir() and any(d.glob("part-*"))] if lake.is_dir() else []
    )
    if not landed:
        return Check(
            "demo data",
            Verdict.OK,
            "not seeded yet — `make demo` will generate and ingest it",
        )
    return Check("demo data", Verdict.OK, f"{len(landed)} source(s) landed in .data/default")


def collect() -> list[Check]:
    checks: list[Check] = [check_python()]
    checks.append(
        check_tool(
            "uv",
            required=True,
            purpose="the Python workspace",
            install="Install uv: https://docs.astral.sh/uv/getting-started/installation/",
        )
    )
    checks.append(
        check_tool(
            "npm",
            required=False,
            purpose="building the SPA outside Docker (`make demo-native`)",
            install="Install Node 22+ if you intend to run the native demo.",
        )
    )
    checks.extend(check_docker_daemon())
    checks.extend(check_port(port, purpose) for port, purpose in sorted(PORTS.items()))
    checks.append(check_disk())
    checks.append(check_settings())
    checks.append(check_fixture_lake())
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doctor", description="Check that this machine can run the platform."
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    checks = collect()

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "verdict": c.verdict.value,
                        "detail": c.detail,
                        "remedy": c.remedy,
                    }
                    for c in checks
                ],
                indent=2,
            )
        )
    else:
        marks = {Verdict.OK: "  ok  ", Verdict.WARN: " warn ", Verdict.FAIL: " FAIL "}
        print("snowobs doctor\n")
        for check in checks:
            print(f"[{marks[check.verdict]}] {check.name:<18} {check.detail}")
            if check.remedy:
                print(f"{'':<26} → {check.remedy}")

    failures = [c for c in checks if c.verdict is Verdict.FAIL]
    warnings = [c for c in checks if c.verdict is Verdict.WARN]
    if not args.json:
        print()
        if failures:
            print(f"{len(failures)} blocking problem(s). Fix those, then re-run `make doctor`.")
        elif warnings:
            print(f"Ready, with {len(warnings)} warning(s) above. `make demo` should work.")
        else:
            print("All clear. Run `make demo`.")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    sys.exit(main())
