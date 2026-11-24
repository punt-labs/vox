"""Unit tests for :mod:`punt_vox.provider_proposal`.

The enable-flow behavior of the proposal (writing the daemon's pick,
reporting a down daemon) is covered end to end in ``test_enablement.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

import pytest

from punt_vox.client_errors import VoxdConnectionError
from punt_vox.config import ConfigStore
from punt_vox.provider_proposal import (
    ENABLE_OUTCOME_REASONS,
    EnableOutcome,
    ProviderProposal,
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.client_sync import VoxClientSync
    from punt_vox.types_provider import ProviderStatusPayload


@final
class _DownClient:
    """A :class:`VoxClientSync` stand-in whose daemon is unreachable."""

    def provider_status(self, provider: str | None = None) -> ProviderStatusPayload:
        _ = provider
        msg = "connection refused"
        raise VoxdConnectionError(msg)


def _down_factory() -> VoxClientSync:
    return cast("VoxClientSync", _DownClient())


def _store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / ".punt-labs" / "vox")


def test_proposal_reports_an_unreachable_daemon_without_writing(
    tmp_path: Path,
) -> None:
    outcome = ProviderProposal(_store(tmp_path), _down_factory).propose_and_write()
    assert outcome.reason == "voxd_unavailable"
    assert outcome.provider_written is None
    assert _store(tmp_path).read().provider is None


def test_proposal_keeps_a_declared_provider(tmp_path: Path) -> None:
    _store(tmp_path).write_field("provider", "polly")
    outcome = ProviderProposal(_store(tmp_path), _down_factory).propose_and_write()
    assert outcome.reason == "already_set"
    assert _store(tmp_path).read().provider == "polly"


def test_enable_outcome_reason_enumeration_is_closed() -> None:
    """Every reason named in the enumeration comment is a member of the frozenset.

    A change to the ``EnableOutcomeReason`` Literal must land in
    :data:`ENABLE_OUTCOME_REASONS` in the same edit; this test proves
    the frozenset is what the Literal claims to be.
    """
    assert (
        frozenset(
            {
                "written",
                "already_set",
                "voxd_unavailable",
                "no_ready_provider",
                "write_failed",
            }
        )
        == ENABLE_OUTCOME_REASONS
    )


def test_enable_outcome_refuses_unknown_reason() -> None:
    """A novel reason string would slip past ``mypy`` (which only knows the Literal).

    The runtime guard on ``__post_init__`` is the closing catch, so a
    consumer branching on ``reason`` never sees a value no branch was
    written for -- the same F4 shape the ``ProviderReadiness`` guard
    exists to prevent.
    """
    with pytest.raises(ValueError, match="unknown enable outcome reason"):
        EnableOutcome(
            reason="mystery",  # type: ignore[arg-type]  # test intent: raise
            provider_written=None,
            detail="",
        )
