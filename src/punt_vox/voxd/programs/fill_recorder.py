"""Persist and announce one fill-generation outcome (record + post as one step).

The :class:`Filler` owns the single-flight task machinery; turning a generation
*result* into a durable :class:`PartEntry` and the matching outcome signal is a
separate concern that lives here, so the filler stays focused on cancellation and
the shield. A ready Part records its file and posts :class:`Produced`; a permanent
failure records a ``FAILED`` entry with its reason (the observable Z ``FillBadPart``
surface) and posts :class:`PermanentFailure`; a transient failure posts
:class:`TransientFailure` and records nothing, so the capped-backoff retry is
untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_programs.identifiers import Reason
from punt_vox.voxd.programs.fill_guard import FreshFillOutcome
from punt_vox.voxd.programs.fill_signal import (
    PermanentFailure,
    Produced,
    TransientFailure,
)
from punt_vox.voxd.programs.manifest import PartEntry
from punt_vox.voxd.programs.part import Part, PartStatus

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.control_channel import ControlChannel
    from punt_vox.voxd.programs.control_signal import ControlSignal
    from punt_vox.voxd.programs.store import PartStore

__all__ = ["FillRecorder"]

logger = logging.getLogger(__name__)


@final
class FillRecorder:
    """Record one fill outcome to the store and post its signal to the channel."""

    __slots__ = ("_channel",)
    _channel: ControlChannel

    def __new__(cls, channel: ControlChannel) -> Self:
        self = super().__new__(cls)
        self._channel = channel
        return self

    def ready(self, store: PartStore, index: int, written: Path) -> None:
        """Record a ready Part and post it to join the pool.

        Logs a symmetric success INFO -- the counterpart to the failure paths'
        WARNING -- so a generated track is visible, not only a failed one.
        """
        entry = PartEntry(index=index, file=written.name, status=PartStatus.READY)
        store.record(entry)
        logger.info("music: generated part %d", index)
        self._post(Produced(Part(written.name, index)))

    def permanent(
        self, store: PartStore, index: int, target: Path, exc: Exception
    ) -> None:
        """Record a permanent per-Part failure and post it (observable surface)."""
        self._failed(store, index, target, self._reason(exc, "permanent"))

    def unexpected(
        self, store: PartStore, index: int, target: Path, exc: Exception
    ) -> None:
        """Record an unexpected (buggy) failure as permanent, logging its trace."""
        logger.error("fill: unexpected error producing part %d", index, exc_info=exc)
        self._failed(store, index, target, Reason(f"unexpected: {exc}"))

    def transient(self, exc: Exception) -> None:
        """Post a transient failure -- nothing recorded, so backoff-retry is intact.

        DEBUG, not WARNING: a transient failure is retried and not yet
        user-actionable, so it stays out of the default log.
        """
        reason = self._reason(exc, "transient")
        logger.debug("music: part transient failure, backing off: %s", reason.text)
        self._post(TransientFailure(reason))

    def _failed(
        self, store: PartStore, index: int, target: Path, reason: Reason
    ) -> None:
        """Record a FAILED entry and post a permanent outcome for ``target``."""
        store.record(
            PartEntry(
                index=index,
                file=target.name,
                status=PartStatus.FAILED,
                reason=reason.text,
            )
        )
        logger.warning("music: part %d failed permanently: %s", index, reason.text)
        self._post(PermanentFailure(Part(target.name, index), reason))

    def _post(self, outcome: ControlSignal) -> None:
        """Post a fill outcome bound to the Program it was generated for.

        Capturing ``channel.source`` here tags the outcome with the source the
        generation ran for: any switch since launch would have cancelled this
        fill (so this method never runs for a switched-away pool), and a switch
        that lands *after* this post is caught by the guard, which drops the
        outcome rather than applying it to the switched-in source.
        """
        self._channel.post(FreshFillOutcome(self._channel.source, outcome))

    @staticmethod
    def _reason(exc: Exception, fallback: str) -> Reason:
        """Build a non-empty Reason from an exception message."""
        return Reason(str(exc) or fallback)
