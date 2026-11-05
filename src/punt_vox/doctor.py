"""Diagnostic health checks for the vox system.

:class:`DoctorCheck` runs each sub-check and gathers a list of
:class:`CheckResult` values. The result type and the render-into-JSON
collection (:class:`~punt_vox.doctor_result.CheckResults`) live in
:mod:`punt_vox.doctor_result`; the mpv, Claude Desktop, and deposited-guide
sub-checks live in :mod:`punt_vox.doctor_mpv`, :mod:`punt_vox.doctor_desktop`,
and :mod:`punt_vox.doctor_guide`.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import TYPE_CHECKING, Self

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdHandshakeRejectedError,
    VoxdProtocolError,
    VoxdRefusedError,
)
from punt_vox.client_sync import VoxClientSync
from punt_vox.dirs import (
    _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
    default_output_dir,
)
from punt_vox.doctor_desktop import DesktopCheck
from punt_vox.doctor_guide import GuideCheck
from punt_vox.doctor_mpv import MpvCheck
from punt_vox.doctor_result import OK, OPTIONAL, CheckResult
from punt_vox.paths import installed_version
from punt_vox.types_provider import ProviderStatusPayload

if TYPE_CHECKING:
    from punt_vox.types_health import HealthStatus

__all__ = ["DoctorCheck"]

# How each connection failure reads, most specific first: the daemon-health
# line (formatted with the error) and the provider-readiness skip reason.
# Only a refusal is "not running"; a rejected handshake is a live voxd whose
# token or origin check failed, so the fix is a restart, never an install.
_CONNECTION_FAILURES: tuple[tuple[type[VoxdConnectionError], str, str], ...] = (
    (
        VoxdRefusedError,
        "Daemon: not running — start with 'vox daemon start'",
        "voxd not running",
    ),
    (
        VoxdHandshakeRejectedError,
        (
            "Daemon: running but rejected the connection (token/origin) — {exc};"
            " run 'vox daemon restart'"
        ),
        "voxd rejected the connection",
    ),
    (VoxdConnectionError, "Daemon: unreachable — {exc}", "voxd unreachable"),
)


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
        results.append(self.check_mpv())
        results.extend(self.check_espeak_fallback())
        results.extend(self.check_daemon_health())
        results.extend(self.check_provider_readiness())
        results.extend(self.check_env_overrides())
        results.extend(self.check_music_dir())
        results.append(self.check_uvx())
        results.extend(self.check_claude_desktop())
        results.extend(self.check_output_dir())
        results.extend(self.check_deposited_guide())
        return results

    # -- individual checks -------------------------------------------------

    def check_python_version(self) -> CheckResult:
        """Check Python >= 3.13."""
        v = sys.version_info
        version_str = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 13):
            return CheckResult.ok(f"Python {version_str}")
        return CheckResult.fail(
            f"Python {version_str} (requires 3.13+)"
            " — install from https://www.python.org/downloads/"
        )

    def check_ffmpeg(self) -> CheckResult:
        """Check ffmpeg is installed -- present/absent verdict, no install path.

        ``ffmpeg`` decodes and transcodes audio; a missing binary is a hard
        error that fails ``vox doctor``.
        """
        if shutil.which("ffmpeg") is None:
            hints: dict[str, str] = {
                "Darwin": "brew install ffmpeg",
                "Windows": "winget install --id Gyan.FFmpeg",
                "default": "see https://ffmpeg.org/download.html",
            }
            hint = hints.get(platform.system(), hints["default"])
            return CheckResult.fail(f"ffmpeg: not found — {hint}")
        return CheckResult.ok("ffmpeg: present")

    def check_mpv(self) -> CheckResult:
        """Check mpv is installed AND at or above the pinned minimum version.

        The check lives in :class:`~punt_vox.doctor_mpv.MpvCheck`; ``doctor``
        holds the run_all schedule and delegates the mpv verdict there.
        """
        return MpvCheck().run()

    def check_espeak_fallback(self) -> list[CheckResult]:
        """Check espeak on Linux when no cloud API keys are set."""
        if platform.system() != "Linux":
            return []
        if any(os.environ.get(k) for k in ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")):
            return []
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            # Out of jail: report presence, never the binary's install path.
            return [CheckResult.ok("espeak: present (offline fallback)")]
        return [
            CheckResult.of(
                OPTIONAL,
                "espeak-ng/espeak: not found — install for offline TTS:"
                " sudo apt-get install espeak-ng",
                required=False,
            )
        ]

    def check_daemon_health(self) -> list[CheckResult]:
        """Check voxd daemon is running and version matches."""
        client = self._client or VoxClientSync()
        try:
            health = client.health()
        except (VoxdConnectionError, VoxdProtocolError) as exc:
            return [self._unanswered(exc)]
        return [self._answered(health)]

    @staticmethod
    def _unanswered(exc: VoxdConnectionError | VoxdProtocolError) -> CheckResult:
        """Return the line for a health request voxd did not answer.

        Only a refused connection is "not running". Anything else on the
        connection -- a voxd that rejected the handshake, a timeout -- means
        something may hold the endpoint, so 'vox daemon install' is not the fix.
        """
        if isinstance(exc, VoxdConnectionError):
            line, _ = DoctorCheck._connection_failure(exc)
            return CheckResult.fail(line.format(exc=exc))
        return CheckResult.fail(f"Daemon: reachable but unhealthy — {exc}")

    @staticmethod
    def _connection_failure(exc: VoxdConnectionError) -> tuple[str, str]:
        """Return the health-line template and readiness reason for ``exc``.

        The table ends on ``VoxdConnectionError`` itself, so every connection
        failure matches a row.
        """
        return next(
            (line, why)
            for kind, line, why in _CONNECTION_FAILURES
            if isinstance(exc, kind)
        )

    @staticmethod
    def _answered(health: HealthStatus) -> CheckResult:
        """Return the line for a voxd that answered, warning on a stale wheel.

        Provider readiness lives on the ``provider_status`` op; the daemon has
        no provider of its own, so the line reports the version and port only.
        """
        running = health.daemon_version
        wheel = installed_version()
        if running and running != wheel:
            return CheckResult.warn(
                f"Daemon: running on port {health.port} (version {running}"
                f" — wheel has {wheel}, run 'vox daemon restart' to refresh)"
            )
        version_note = f", version {running}" if running else ""
        return CheckResult.ok(f"Daemon: running on port {health.port}{version_note}")

    def check_provider_readiness(self) -> list[CheckResult]:
        """Report the daemon's readiness verdict for every registered provider.

        Every credentials error message points here (``... run `vox doctor```).
        The daemon owns the verdict -- ``ProviderCredentials.report_all`` walks
        the same requirement dispatch the resolution gate uses, so a provider
        this reports ready is exactly a provider ``mic:unmute`` will not refuse
        for credentials reasons, and vice versa. An unready provider is a
        warning rather than a hard fail because a single-provider host is a
        normal configuration; only the state-declared provider being
        unavailable is fatal, and that surfaces at synthesis.

        The caller-side environment is deliberately NOT probed: voxd is a
        detached service with its own environment, so answering from this
        process would report on the wrong one. Voxd unreachable is therefore
        reported once at the section head and the per-provider walk skipped.
        """
        client = self._client or VoxClientSync()
        try:
            payload = client.provider_status()
        except VoxdConnectionError as exc:
            # Daemon down or unreachable is already a fail from
            # ``check_daemon_health``; here it would be a duplicate hard-fail
            # with the same cause. Report it as informational so the
            # readiness section explains why it is empty.
            _, why = self._connection_failure(exc)
            return [
                CheckResult.of(
                    OPTIONAL, f"Provider readiness: skipped ({why})", required=False
                )
            ]
        except VoxdProtocolError as exc:
            return [CheckResult.warn(f"Provider readiness: unavailable — {exc}")]
        return self._render_provider_readiness(payload)

    @staticmethod
    def _render_provider_readiness(
        payload: ProviderStatusPayload,
    ) -> list[CheckResult]:
        """Turn a ``provider_status`` payload into per-provider check lines.

        ``preferred`` leads the section so the user sees the daemon's
        proposed default before the walk of individual verdicts; a
        ``None`` ``preferred`` (no provider on this host is ready --
        no keys, no platform binary) is itself a hard fail because it
        is a genuinely unusable configuration for TTS.
        """
        results: list[CheckResult] = []
        if payload.preferred is None:
            results.append(
                CheckResult.fail(
                    "Provider readiness: no provider is usable on this daemon"
                    " — set at least one credential (e.g. ELEVENLABS_API_KEY,"
                    " OPENAI_API_KEY, or AWS credentials)"
                )
            )
        else:
            results.append(
                CheckResult.ok(f"Provider readiness: preferred → {payload.preferred}")
            )
        for row in payload.providers:
            if row.ready:
                results.append(CheckResult.ok(f"  {row.name}: ready"))
            else:
                # ``detail`` names the missing variables (F2) or is empty for
                # ``unknown_provider`` (F4); either way it is the same string
                # the wire error would carry, so ``doctor`` and ``mic:unmute``
                # cannot tell the user two different reasons.
                message = f"  {row.name}: not ready"
                if row.detail:
                    message = f"{message} — {row.detail}"
                results.append(CheckResult.of(OPTIONAL, message, required=False))
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
            return [CheckResult.ok(f"Remote config: {', '.join(overrides)}")]
        return []

    def check_music_dir(self) -> list[CheckResult]:
        """Check the music dir -- present/absent verdict, no abs path.

        An in-jail path under the ``output`` root, reported as a relative verdict
        so its absolute prefix never crosses to a client.
        """
        music_dir = _resolve_music_dir()  # pyright: ignore[reportPrivateUsage]
        if not music_dir.is_dir():
            return [
                CheckResult.warn(
                    "output music dir: absent — created on first 'vox record'"
                )
            ]
        return []

    def check_uvx(self) -> CheckResult:
        """Check for uvx -- present/absent verdict, no host path (out of jail)."""
        if shutil.which("uvx"):
            return CheckResult.of(OK, "uvx: present", required=False)
        return CheckResult.of(
            OPTIONAL,
            "uvx: not found (needed for MCP server)",
            required=False,
        )

    def check_claude_desktop(self) -> list[CheckResult]:
        """Check Claude Desktop config and MCP registration.

        The check lives in :class:`~punt_vox.doctor_desktop.DesktopCheck`;
        ``doctor`` holds the run_all schedule and delegates the verdict there.
        """
        return DesktopCheck().run()

    def check_output_dir(self) -> list[CheckResult]:
        """Check the output dir -- writable verdict, no abs path or raw OSError.

        The dir *is* the ``output`` data root, so its own label is the name; the
        absolute path and the raw ``OSError`` stay out of the client-facing reply.
        """
        out_dir = default_output_dir()
        if out_dir.is_dir():
            try:
                test_file = out_dir / ".doctor_test"
                test_file.write_text("ok")
                test_file.unlink()
                return [CheckResult.ok("output: writable")]
            except OSError:
                return [CheckResult.fail("output: not writable — check permissions")]
        return [CheckResult.warn("output: absent — created on first 'vox record'")]

    def check_deposited_guide(self) -> list[CheckResult]:
        """Check the per-repo deposited guide against the packaged asset.

        The check lives in :class:`~punt_vox.doctor_guide.GuideCheck`;
        ``doctor`` holds the run_all schedule and delegates the verdict there.
        """
        return GuideCheck().run()
