"""Confirm each ready provider's credentials with one authenticated live call.

``ProviderCredentials`` answers readiness from a cheap local check -- a key is
in the environment, boto3 resolved some credentials -- because the synthesis
gate runs it on every request.  That check cannot tell a stale ElevenLabs key,
a revoked OpenAI key or expired AWS credentials from good ones, so ``vox
doctor`` reported them ``ready``.  :class:`LiveCheck` closes that gap
for the diagnostic path only: it runs each ready provider's own
``check_health`` -- the ElevenLabs subscription read, the OpenAI model
retrieve, the STS identity and Polly voice list -- and turns a failed required
check into a ``check_failed`` row carrying the provider's reason.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.providers import get_provider
from punt_vox.types_provider import ProviderReadiness

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from punt_vox.types import HealthCheck, TTSProvider

__all__ = ["LiveCheck"]

logger = logging.getLogger(__name__)

# Each probe is one or two small HTTPS calls; an answer slower than this is a
# network problem worth reporting, not one worth making doctor wait out.
_DEFAULT_DEADLINE_S: Final = 10.0


@final
class LiveCheck:
    """Downgrade each ready row whose provider rejects its live health check.

    *build* constructs the provider by name (production: the default
    registry); it is injected so tests substitute stub providers rather than
    patching SDKs.  Probes run concurrently in worker threads, each bounded by
    *deadline_s*, so one slow provider cannot stall the whole report.
    """

    __slots__ = ("_build", "_deadline_s")
    _build: Callable[[str], TTSProvider]
    _deadline_s: float

    def __new__(
        cls,
        build: Callable[[str], TTSProvider] | None = None,
        deadline_s: float = _DEFAULT_DEADLINE_S,
    ) -> Self:
        """Create a live check that builds providers with *build*."""
        self = super().__new__(cls)
        self._build = build if build is not None else get_provider
        self._deadline_s = deadline_s
        return self

    async def confirm_all(
        self, rows: Sequence[ProviderReadiness]
    ) -> tuple[ProviderReadiness, ...]:
        """Return *rows* in order, each ready one confirmed by a live check."""
        return tuple(await asyncio.gather(*(self.confirm(row) for row in rows)))

    async def confirm(self, row: ProviderReadiness) -> ProviderReadiness:
        """Return *row*, or a ``check_failed`` row if its live check fails.

        A row that is already not ready is returned untouched: there is no key
        to send.  The check never raises: a raise is logged and reported in the
        row by exception type only, since provider error text can echo request
        material.
        """
        if not row.ready:
            return row
        try:
            checks = await asyncio.wait_for(
                asyncio.to_thread(self._health, row.name), self._deadline_s
            )
        except TimeoutError:
            return self._failed(
                row, f"live check got no answer within {self._deadline_s:g}s"
            )
        except Exception as exc:
            logger.warning("live check of %s raised", row.name, exc_info=True)
            return self._failed(row, f"live check raised {type(exc).__name__}")
        failures = [
            check.message for check in checks if check.required and not check.passed
        ]
        if not failures:
            return row
        return self._failed(row, "; ".join(failures))

    def _health(self, name: str) -> list[HealthCheck]:
        return self._build(name).check_health()

    @staticmethod
    def _failed(row: ProviderReadiness, detail: str) -> ProviderReadiness:
        return ProviderReadiness(
            name=row.name, ready=False, reason="check_failed", detail=detail
        )
