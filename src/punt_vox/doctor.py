"""Diagnostic health checks for the vox system."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from punt_vox.client import VoxClientSync, VoxdConnectionError, VoxdProtocolError
from punt_vox.dirs import default_output_dir
from punt_vox.paths import installed_version

__all__ = [
    "CheckResult",
    "DoctorCheck",
]

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_OK = "✓"
_FAIL = "✗"
_OPTIONAL = "○"
_WARN = "⚠"

_STATUS_KIND: dict[str, str] = {
    _OK: "pass",
    _FAIL: "fail",
    _OPTIONAL: "skip",
    _WARN: "warn",
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a single diagnostic check."""

    name: str
    passed: bool
    message: str
    detail: str = ""
    required: bool = True
    symbol: str = _OK
    status_kind: str = "pass"


# ---------------------------------------------------------------------------
# DoctorCheck
# ---------------------------------------------------------------------------


class DoctorCheck:
    """Run all diagnostic checks for the vox system."""

    __slots__ = ("_client",)
    _client: VoxClientSync | None

    def __new__(cls, client: VoxClientSync | None = None) -> Self:
        self = super().__new__(cls)
        self._client = client
        return self

    # -- public API --------------------------------------------------------

    def run_all(self) -> list[CheckResult]:
        """Execute every check and return results in order."""
        results: list[CheckResult] = []
        results.append(self.check_python_version())
        results.append(self.check_ffmpeg())
        results.extend(self.check_espeak_fallback())
        results.extend(self.check_daemon_health())
        results.extend(self.check_env_overrides())
        results.extend(self.check_legacy_output_dir())
        results.extend(self.check_music_dir())
        results.extend(self.check_legacy_user_unit())
        results.append(self.check_uvx())
        results.extend(self.check_claude_desktop())
        results.extend(self.check_output_dir())
        return results

    # -- individual checks -------------------------------------------------

    def check_python_version(self) -> CheckResult:
        """Check Python >= 3.13."""
        v = sys.version_info
        version_str = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 13):
            return _pass(f"Python {version_str}")
        return _fail(
            f"Python {version_str} (requires 3.13+)"
            " — install from https://www.python.org/downloads/"
        )

    def check_ffmpeg(self) -> CheckResult:
        """Check ffmpeg is installed."""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return _pass(f"ffmpeg: {ffmpeg}")
        hint = {
            "Darwin": "brew install ffmpeg",
            "Linux": "see https://ffmpeg.org/download.html",
            "Windows": "winget install --id Gyan.FFmpeg",
        }.get(platform.system(), "see https://ffmpeg.org/download.html")
        return _fail(f"ffmpeg: not found — {hint}")

    def check_espeak_fallback(self) -> list[CheckResult]:
        """Check espeak on Linux when no cloud API keys are set."""
        if platform.system() != "Linux":
            return []
        if any(os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")):
            return []
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if espeak:
            espeak_name = Path(espeak).name
            return [_pass(f"{espeak_name}: {espeak} (offline fallback)")]
        return [
            _result(
                _OPTIONAL,
                "espeak-ng/espeak: not found — install for offline TTS:"
                " sudo apt-get install espeak-ng",
                required=False,
            )
        ]

    def check_daemon_health(self) -> list[CheckResult]:
        """Check voxd daemon is running and version matches."""
        results: list[CheckResult] = []
        client = self._client or VoxClientSync()
        try:
            health = client.health()
        except VoxdConnectionError:
            results.append(
                _fail("Daemon: not running — start with 'vox daemon install'")
            )
            return results
        except VoxdProtocolError as exc:
            results.append(_fail(f"Daemon: reachable but unhealthy — {exc}"))
            return results

        provider_name = str(health.get("provider", "unknown"))
        port = health.get("port", "?")
        running_version = str(health.get("daemon_version", ""))
        wheel_version = installed_version()

        if running_version and running_version != wheel_version:
            results.append(
                _warn(
                    f"Daemon: running on port {port} (version {running_version}"
                    f" — wheel has {wheel_version},"
                    f" run 'vox daemon restart' to refresh)"
                )
            )
        else:
            version_note = f", version {running_version}" if running_version else ""
            results.append(
                _pass(
                    f"Daemon: running on port {port}"
                    f" (provider: {provider_name}{version_note})"
                )
            )
        return results

    def check_env_overrides(self) -> list[CheckResult]:
        """Report active VOXD_* environment variable overrides."""
        overrides: list[str] = []
        for env_name in ("VOXD_HOST", "VOXD_PORT", "VOXD_TOKEN"):
            env_val = os.environ.get(env_name, "").strip()
            if env_val:
                display = "***" if env_name == "VOXD_TOKEN" else env_val
                overrides.append(f"{env_name}={display}")
        if overrides:
            return [_pass(f"Remote config: {', '.join(overrides)}")]
        return []

    def check_legacy_output_dir(self) -> list[CheckResult]:
        """Check for legacy ~/vox-output/ directory."""
        legacy_output = Path.home() / "vox-output"
        if not legacy_output.is_dir():
            return []
        try:
            count = sum(1 for p in legacy_output.rglob("*") if p.is_file())
        except OSError:
            count = -1

        if count > 0:
            return [
                _warn(
                    f"Legacy audio: ~/vox-output contains {count} file(s)"
                    " — run 'vox migrate-audio' to move to ~/Music/vox/"
                )
            ]
        if count == 0:
            return [
                _warn(
                    "Legacy audio: ~/vox-output exists but is empty"
                    " — safe to remove: rm -rf ~/vox-output"
                )
            ]
        return [
            _warn(
                "Legacy audio: ~/vox-output exists but is unreadable"
                " — check permissions"
            )
        ]

    def check_music_dir(self) -> list[CheckResult]:
        """Check music directory existence."""
        from punt_vox.dirs import (
            _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
        )

        music_dir = _resolve_music_dir()  # pyright: ignore[reportPrivateUsage]
        if not music_dir.is_dir():
            return [
                _warn(
                    f"Music directory: {music_dir} does not exist"
                    " — will be created on first 'vox record'"
                )
            ]
        return []

    def check_legacy_user_unit(self) -> list[CheckResult]:
        """Check for stale systemd user-level vox.service (Linux only)."""
        from punt_vox.service import (
            _legacy_user_unit_path,  # pyright: ignore[reportPrivateUsage]
        )

        legacy_unit = _legacy_user_unit_path()
        if platform.system() != "Linux" or not legacy_unit.exists():
            return []

        referenced = _parse_user_unit_execstart_subcommand(legacy_unit)
        valid = _valid_vox_subcommands()
        quoted_legacy_unit = shlex.quote(str(legacy_unit))
        remediation_command = (
            "systemctl --user disable --now vox.service"
            f" && rm {quoted_legacy_unit}"
            " && systemctl --user daemon-reload"
        )

        if referenced is None:
            return [
                _fail(
                    f"Legacy user unit: {legacy_unit} exists but ExecStart= is"
                    " unparseable — run 'vox daemon install' to clean it up,"
                    " or remove it manually:\n"
                    f"  {remediation_command}"
                )
            ]
        if referenced not in valid:
            return [
                _fail(
                    f"Legacy user unit: {legacy_unit} references"
                    f" 'vox {referenced}', which is not a current subcommand"
                    " (this unit will crash-loop on the systemd restart schedule)."
                    " Run 'vox daemon install' to clean it up, or remove it"
                    " manually:\n"
                    f"  {remediation_command}"
                )
            ]
        return [
            _pass(
                f"Legacy user unit: {legacy_unit} references current"
                f" 'vox {referenced}' subcommand"
            )
        ]

    def check_uvx(self) -> CheckResult:
        """Check for uvx binary."""
        uvx = shutil.which("uvx")
        if uvx:
            return _result(_OK, f"uvx: {uvx}", required=False)
        return _result(
            _OPTIONAL,
            "uvx: not found (needed for MCP server)",
            required=False,
        )

    def check_claude_desktop(self) -> list[CheckResult]:
        """Check Claude Desktop config and MCP registration."""
        results: list[CheckResult] = []
        config_path = claude_desktop_config_path()

        if not config_path.exists():
            results.append(
                _result(
                    _OPTIONAL,
                    "Claude Desktop config: not found",
                    required=False,
                )
            )
            results.append(
                _result(
                    _OPTIONAL,
                    "Claude Desktop MCP: not registered (run 'vox install-desktop')",
                    required=False,
                )
            )
            return results

        results.append(
            _result(
                _OK,
                f"Claude Desktop config: {config_path}",
                required=False,
            )
        )

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            if "tts" in servers:
                results.append(
                    _result(
                        _OK,
                        "Claude Desktop MCP: registered",
                        required=False,
                    )
                )
            else:
                results.append(
                    _result(
                        _OPTIONAL,
                        "Claude Desktop MCP: not registered"
                        " (run 'vox install-desktop')",
                        required=False,
                    )
                )
        except (json.JSONDecodeError, OSError):
            results.append(
                _result(
                    _OPTIONAL,
                    "Claude Desktop MCP: could not read config",
                    required=False,
                )
            )
        return results

    def check_output_dir(self) -> list[CheckResult]:
        """Check output directory writability."""
        out_dir = default_output_dir()
        if out_dir.is_dir():
            try:
                test_file = out_dir / ".doctor_test"
                test_file.write_text("ok")
                test_file.unlink()
                return [_pass(f"Output directory: {out_dir}")]
            except OSError as exc:
                return [
                    _fail(
                        f"Output directory: {out_dir} ({exc})"
                        " — check permissions or use --output-dir"
                    )
                ]
        return [
            _warn(
                f"Output directory: {out_dir} does not exist"
                " — will be created on first 'vox record'"
            )
        ]


# ---------------------------------------------------------------------------
# Helpers shared between DoctorCheck and __main__.py
# ---------------------------------------------------------------------------


def claude_desktop_config_path() -> Path:
    """Return the Claude Desktop config file path."""
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )


def _valid_vox_subcommands() -> set[str]:
    """Return the set of subcommand tokens the current CLI accepts.

    Includes both leaf commands and subcommand groups so a unit that
    references ``vox daemon`` or ``vox cache`` parses as valid.
    """
    # Lazy import to avoid circular dependency (doctor.py is imported
    # by __main__.py which defines the app).
    from punt_vox.__main__ import app

    leaf_names: set[str] = set()
    for command in app.registered_commands:
        raw_name = command.callback.__name__ if command.callback else None
        name = command.name or (raw_name.replace("_", "-") if raw_name else None)
        if name:
            leaf_names.add(name)
    group_names = {g.name for g in app.registered_groups if g.name}
    return leaf_names | group_names


def _parse_user_unit_execstart_subcommand(unit_path: Path) -> str | None:
    """Extract the first CLI subcommand token from a systemd unit file.

    Reads the unit file, joins line continuations, finds the first
    ``ExecStart=`` directive, shell-splits it, and returns the first
    non-flag token after the binary path. Returns None when the file
    is unreadable or unparseable.
    """
    try:
        content = unit_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Join systemd line continuations (trailing backslash).
    joined_lines: list[str] = []
    buffer = ""
    for raw in content.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined_lines.append(buffer + raw)
        buffer = ""
    if buffer:
        joined_lines.append(buffer)

    exec_line: str | None = None
    for raw in joined_lines:
        line = raw.strip()
        if line.startswith("ExecStart="):
            exec_line = line[len("ExecStart=") :].strip()
            break

    if not exec_line:
        return None

    # Strip systemd execution prefixes (-, @, +, !, !!).
    while exec_line[:1] in {"-", "@", "+", "!"}:
        exec_line = exec_line[1:].lstrip()

    if not exec_line:
        return None

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None

    for token in tokens[1:]:
        if not token.startswith("-"):
            return token
    return None


def format_results(results: list[CheckResult]) -> tuple[dict[str, object], str]:
    """Format check results into JSON payload and display text.

    Returns a (payload, text) tuple matching the existing ``doctor``
    command output format.
    """
    passed = 0
    failed = 0
    warned = 0
    lines: list[str] = []
    checks: list[dict[str, object]] = []

    for r in results:
        lines.append(f"{r.symbol} {r.message}")
        checks.append(
            {
                "status": r.symbol,
                "status_kind": r.status_kind,
                "message": r.message,
                "required": r.required,
                "passed": r.passed,
            }
        )
        if r.passed:
            passed += 1
        elif r.symbol == _FAIL and r.required:
            failed += 1
        elif r.symbol == _WARN:
            warned += 1

    summary = f"{passed} passed, {failed} failed"
    if warned > 0:
        summary += f", {warned} warning" + ("s" if warned > 1 else "")
    text_parts = ["=" * 40, *lines, "=" * 40, summary]

    payload: dict[str, object] = {
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "checks": checks,
    }
    return payload, "\n".join(text_parts)


# ---------------------------------------------------------------------------
# Private result constructors
# ---------------------------------------------------------------------------


def _pass(message: str) -> CheckResult:
    """Create a passing check result."""
    return CheckResult(
        name=message,
        passed=True,
        message=message,
        symbol=_OK,
        status_kind="pass",
    )


def _fail(message: str) -> CheckResult:
    """Create a failing check result."""
    return CheckResult(
        name=message,
        passed=False,
        message=message,
        symbol=_FAIL,
        status_kind="fail",
    )


def _warn(message: str) -> CheckResult:
    """Create a warning check result."""
    return CheckResult(
        name=message,
        passed=False,
        message=message,
        symbol=_WARN,
        status_kind="warn",
    )


def _result(symbol: str, message: str, *, required: bool = True) -> CheckResult:
    """Create a check result with an explicit symbol."""
    return CheckResult(
        name=message,
        passed=symbol == _OK,
        message=message,
        symbol=symbol,
        status_kind=_STATUS_KIND.get(symbol, "fail"),
        required=required,
    )
