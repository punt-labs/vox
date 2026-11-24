"""Tests for :mod:`punt_vox.providers.live_check` -- doctor's authenticated probe.

A key that is merely present is not a key the provider accepts: each
ready row is confirmed by the provider's own authenticated health check, and a
failure, a raise or a hang turns the row ``check_failed`` with the reason.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, final

from punt_vox.providers.live_check import LiveCheck
from punt_vox.types import HealthCheck
from punt_vox.types_provider import ProviderReadiness

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.types import TTSProvider


@final
@dataclass(frozen=True, slots=True)
class _Provider:
    """A provider stub whose health check runs *check*."""

    check: Callable[[], list[HealthCheck]]

    def check_health(self) -> list[HealthCheck]:
        return self.check()


def _builder(
    checks: dict[str, Callable[[], list[HealthCheck]]],
) -> Callable[[str], TTSProvider]:
    def build(name: str) -> TTSProvider:
        return cast("TTSProvider", _Provider(checks[name]))

    return build


def _ready(name: str) -> ProviderReadiness:
    return ProviderReadiness(name=name, ready=True, reason="ok", detail="")


def _rejected() -> list[HealthCheck]:
    return [
        HealthCheck(passed=True, message="ElevenLabs API key: set"),
        HealthCheck(
            passed=False,
            message="ElevenLabs subscription: API key rejected (HTTP 401)",
        ),
    ]


def _accepted() -> list[HealthCheck]:
    return [HealthCheck(passed=True, message="OpenAI model access: tts-1")]


class TestLiveCheck:
    def test_rejected_key_is_not_ready(self) -> None:
        check = LiveCheck(build=_builder({"elevenlabs": _rejected}))
        (row,) = asyncio.run(check.confirm_all((_ready("elevenlabs"),)))
        assert row == ProviderReadiness(
            name="elevenlabs",
            ready=False,
            reason="check_failed",
            detail="ElevenLabs subscription: API key rejected (HTTP 401)",
        )

    def test_accepted_key_stays_ready(self) -> None:
        check = LiveCheck(build=_builder({"openai": _accepted}))
        rows = asyncio.run(check.confirm_all((_ready("openai"),)))
        assert rows == (_ready("openai"),)

    def test_optional_failure_does_not_unready(self) -> None:
        def advisory() -> list[HealthCheck]:
            return [HealthCheck(passed=False, message="advisory", required=False)]

        check = LiveCheck(build=_builder({"polly": advisory}))
        rows = asyncio.run(check.confirm_all((_ready("polly"),)))
        assert rows == (_ready("polly"),)

    def test_unready_row_is_not_probed(self) -> None:
        def explode(name: str) -> TTSProvider:
            raise AssertionError(name)

        row = ProviderReadiness(
            name="openai", ready=False, reason="no_credentials", detail="no key"
        )
        rows = asyncio.run(LiveCheck(build=explode).confirm_all((row,)))
        assert rows == (row,)

    def test_raising_check_reports_its_type(self) -> None:
        def boom() -> list[HealthCheck]:
            msg = "secret-bearing text must not surface"
            raise RuntimeError(msg)

        check = LiveCheck(build=_builder({"polly": boom}))
        (row,) = asyncio.run(check.confirm_all((_ready("polly"),)))
        assert row.reason == "check_failed"
        assert row.detail == "live check raised RuntimeError"

    def test_hung_check_times_out(self) -> None:
        release = threading.Event()

        def hang() -> list[HealthCheck]:
            release.wait(5)
            return _accepted()

        check = LiveCheck(build=_builder({"openai": hang}), deadline_s=0.05)
        try:
            (row,) = asyncio.run(check.confirm_all((_ready("openai"),)))
        finally:
            release.set()
        assert row.reason == "check_failed"
        assert row.detail == "live check got no answer within 0.05s"

    def test_rows_keep_their_order(self) -> None:
        check = LiveCheck(
            build=_builder({"elevenlabs": _rejected, "openai": _accepted})
        )
        rows = asyncio.run(check.confirm_all((_ready("elevenlabs"), _ready("openai"))))
        assert [r.name for r in rows] == ["elevenlabs", "openai"]
        assert [r.ready for r in rows] == [False, True]

    def test_probe_runs_on_its_own_pool(self) -> None:
        # A hung probe holds its thread past the deadline, so it must not sit
        # in the loop's default executor that synthesis also uses.
        names: list[str] = []

        def record_thread() -> list[HealthCheck]:
            names.append(threading.current_thread().name)
            return _accepted()

        check = LiveCheck(build=_builder({"openai": record_thread}))
        asyncio.run(check.confirm_all((_ready("openai"),)))
        assert names[0].startswith("live-check")

    def test_probes_wait_for_a_callers_key_to_leave_the_environment(self) -> None:
        # Synthesis holds this lock while a caller's own key is in os.environ;
        # a probe built then would check the caller's key, not the daemon's.
        probed: list[str] = []

        def record() -> list[HealthCheck]:
            probed.append("openai")
            return _accepted()

        async def scenario() -> None:
            env_lock = asyncio.Lock()
            check = LiveCheck(build=_builder({"openai": record}), env_lock=env_lock)
            async with env_lock:
                task = asyncio.create_task(check.confirm_all((_ready("openai"),)))
                await asyncio.sleep(0.05)
                assert probed == []
            assert (await task)[0].ready
            assert probed == ["openai"]

        asyncio.run(scenario())
