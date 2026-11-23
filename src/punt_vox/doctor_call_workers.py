"""Advisory diagnostics for the host tools that launch call workers."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Self, final

from punt_vox.doctor_result import OK, WARN, CheckResult

_MIN_SYSTEMD_VERSION = 254


@final
class CallWorkerChecks:
    """Check the worker launcher without starting a worker."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the worker prerequisite checks."""
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Report tmux and the worker launcher on Linux systemd hosts."""
        results = [self._tmux()]
        if sys.platform == "linux" and Path("/run/systemd/system").is_dir():
            results.append(self._systemd_launcher())
        return results

    @staticmethod
    def _tmux() -> CheckResult:
        if shutil.which("tmux"):
            return CheckResult.of(OK, "tmux: present", required=False)
        return CheckResult.of(
            WARN, "tmux: not found — install it to launch call workers", required=False
        )

    def _systemd_launcher(self) -> CheckResult:
        if (path := shutil.which("systemd-run")) is None:
            return CheckResult.of(
                WARN,
                "systemd-run: not found — call workers launched by the Vox "
                f"service need it (systemd {_MIN_SYSTEMD_VERSION}+)",
                required=False,
            )
        try:
            output = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
                [path, "--version"],
                check=True,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            ).stdout
        except subprocess.TimeoutExpired:
            return self._unreadable("the command timed out")
        except subprocess.CalledProcessError as exc:
            return self._unreadable(f"the command exited {exc.returncode}")
        except OSError:
            return self._unreadable("the command could not run")
        return self._version_result(output)

    def _version_result(self, output: str) -> CheckResult:
        match = re.match(r"systemd\s+([0-9]+)\b", output)
        if match is None:
            return self._unreadable("unexpected version output")
        version = int(match[1])
        if version < _MIN_SYSTEMD_VERSION:
            return CheckResult.of(
                WARN,
                f"systemd version: {version} — call workers launched by the Vox "
                f"service require systemd {_MIN_SYSTEMD_VERSION}+ "
                "(e.g. Ubuntu 24.04 LTS)",
                required=False,
            )
        return CheckResult.of(
            OK,
            f"systemd version: {version} (meets the {_MIN_SYSTEMD_VERSION}+ minimum)",
            required=False,
        )

    @staticmethod
    def _unreadable(reason: str) -> CheckResult:
        return CheckResult.of(
            WARN,
            f"systemd version: unreadable — {reason}; run 'systemd-run --version'",
            required=False,
        )
