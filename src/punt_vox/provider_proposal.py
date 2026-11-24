"""The daemon-proposal step of ``vox enable``: pick and record a starter provider.

:class:`ProviderProposal` is the one collaborator that reaches outside the
repo: it opens a WebSocket to voxd, calls the ``provider_status`` op, and
writes ``preferred`` into ``vox.md`` via :class:`ConfigStore`.  A daemon that
is unreachable, a payload that reports no provider is ready, or a repo whose
``vox.md`` already names a provider are all reported without writing --
enable never overrules an existing choice, and it never guesses when the
daemon has no answer to give.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_sync import VoxClientSync
from punt_vox.config import DURABLE_FILENAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.config import ConfigStore

logger = logging.getLogger(__name__)

__all__ = [
    "ENABLE_OUTCOME_REASONS",
    "EnableOutcome",
    "EnableOutcomeReason",
    "ProviderProposal",
]

# The transport-side faults ``ProviderProposal`` treats as "voxd not reachable".
# Deliberately narrower than the tuple ``server.py`` uses: this catch sits ABOVE
# :class:`~punt_vox.client_sync.VoxClientSync`, which already wraps a
# ``websockets.exceptions.WebSocketException`` into
# :class:`~punt_vox.client_errors.VoxdConnectionError` before it reaches here, so
# adding ``WebSocketException`` to this tuple would catch nothing new -- the
# client boundary has already normalised it.  ``server.py`` sits BELOW that
# boundary (some server paths open the socket directly) and therefore still has
# to name it.  ``OSError`` is included on both sides so a socket-level failure
# (address in use, no route to host) folds into the same reply rather than
# escaping the tool boundary.
_DAEMON_ERRORS: tuple[type[BaseException], ...] = (
    VoxdConnectionError,
    VoxdProtocolError,
    OSError,
)


# The closed set of reasons ``EnableOutcome`` can carry, and where each
# value is produced.  Same enumeration discipline as
# :data:`~punt_vox.types_provider.ProviderStatusReason`: prose next to a
# Literal rots the first time a new producer is added
# pressure, so the runtime frozenset below is the load-bearing check and
# :meth:`EnableOutcome.__post_init__` refuses a value not in either.  A
# new reason -- or a new producer of an existing reason -- must land at
# the same time in the Literal, the frozenset, the site that produces
# it, and every consumer that branches on it (currently
# :meth:`ProviderProposal.propose_and_write` is the only in-tree
# brancher, on ``reason != "written"``).
#     written           ProviderProposal.propose_and_write on a successful
#                       daemon proposal + vox.md write
#     already_set       ProviderProposal.propose_and_write when vox.md
#                       already declares a provider (never overrule a
#                       human's choice)
#     voxd_unavailable  ProviderProposal._ask_daemon on _DAEMON_ERRORS
#                       (the provider_status op could not be reached)
#     no_ready_provider ProviderProposal._ask_daemon when the daemon
#                       replied but ``preferred is None``
#     write_failed      ProviderProposal.propose_and_write on OSError
#                       writing the proposed provider into vox.md; the
#                       daemon answered correctly, the local disk write
#                       is what failed (permission denied, ENOSPC, ...)
type EnableOutcomeReason = Literal[
    "written",
    "already_set",
    "voxd_unavailable",
    "no_ready_provider",
    "write_failed",
]

# Runtime mirror of the Literal above; the guard on
# :meth:`EnableOutcome.__post_init__` refuses a mismatch.
ENABLE_OUTCOME_REASONS: frozenset[str] = frozenset(
    {"written", "already_set", "voxd_unavailable", "no_ready_provider", "write_failed"}
)


@dataclass(frozen=True, slots=True)
class EnableOutcome:
    """What :meth:`RepoEnablement.enable` did with the provider slot.

    Every enable, whether it wrote or not, produces one of these so
    the CLI and MCP surfaces can report the same story.  The value is
    additive to the file operations -- guide, import, settings,
    marker land regardless -- and reports only what happened at the
    daemon-proposal step.

    ``reason`` is one of :data:`EnableOutcomeReason`; the comment
    above the Literal names each value's producer.  Do not widen this
    field to cover two failure classes with one string:
    ``voxd_unavailable`` means the daemon could not be reached and
    the fix is to start it, whereas ``write_failed`` means the daemon
    answered fine but the local ``vox.md`` write failed and the fix
    is a local one (permissions, disk space).
    """

    reason: EnableOutcomeReason
    provider_written: str | None
    detail: str

    def __post_init__(self) -> None:
        """Refuse a ``reason`` not in :data:`ENABLE_OUTCOME_REASONS`.

        A novel string would slip past ``mypy`` (which only knows the
        Literal) and land at a consumer with no branch for it -- the
        same shape as the F4 defect this discipline exists to prevent.
        """
        if self.reason not in ENABLE_OUTCOME_REASONS:
            msg = (
                f"unknown enable outcome reason {self.reason!r}; "
                f"expected one of {sorted(ENABLE_OUTCOME_REASONS)}"
            )
            raise ValueError(msg)


@final
class ProviderProposal:
    """Ask the daemon for a preferred provider and write it into ``vox.md``.

    The one collaborator that reaches outside the repo.  Kept apart
    from :class:`RepoEnablement` so the state machine's file
    operations stay pure and testable without a daemon, and so the
    daemon-proposal step is composable: a test binds a fake client
    factory, production binds :class:`VoxClientSync`.

    The write is guarded by two invariants:
    :meth:`ConfigStore.read_field` returning a non-empty provider name
    is a human's declared choice and is never overruled (``already_set``);
    a daemon that returns ``preferred is None`` reports rather than
    writes (``no_ready_provider``), because writing an empty string would
    be a lie about state.
    """

    __slots__ = ("_client_factory", "_store")
    _store: ConfigStore
    _client_factory: Callable[[], VoxClientSync]

    def __new__(
        cls,
        store: ConfigStore,
        client_factory: Callable[[], VoxClientSync] = VoxClientSync,
    ) -> Self:
        """Create a provider proposal."""
        self = super().__new__(cls)
        self._store = store
        self._client_factory = client_factory
        return self

    def propose_and_write(self) -> EnableOutcome:
        """Do the daemon round-trip and, if warranted, write the provider.

        Order matters: the ``already_set`` guard runs BEFORE the
        daemon call, so a repo whose ``vox.md`` already declares a
        provider never even reaches the daemon -- there is nothing to
        write, and asking would be a wasted round-trip that also
        raised the failure surface (a daemon glitch would flip
        ``already_set`` to ``voxd_unavailable`` for no gain).

        An :class:`OSError` at the local write path becomes a
        ``write_failed`` outcome, distinct from ``voxd_unavailable``:
        the daemon answered fine, the failure is on the local disk.
        The detail carries the target path and the ``OSError`` text so
        the user can act on it (a bare "could not write" without the
        path sends them to hunt for the file).
        """
        existing = self._existing_provider()
        if existing is not None:
            return EnableOutcome(
                reason="already_set",
                provider_written=None,
                detail=f"vox.md already declares provider={existing!r}",
            )
        preferred = self._ask_daemon()
        if preferred.reason != "written" or preferred.provider_written is None:
            # ``_ask_daemon`` only sets ``reason == "written"`` when it has
            # a provider name to hand back; the ``or`` branch keeps ``mypy``
            # honest about the narrowing rather than smuggling an ``assert``
            # past the boundary (PY-EH-3, ruff S101).
            return preferred
        provider = preferred.provider_written
        target = self._store.dir / DURABLE_FILENAME
        try:
            self._store.write_field("provider", provider)
        except OSError as exc:
            logger.exception("enable: failed to write proposed provider")
            return EnableOutcome(
                reason="write_failed",
                provider_written=None,
                detail=(
                    f"could not write provider={provider!r} to {target}: "
                    f"{exc.__class__.__name__}: {exc}"
                ),
            )
        return EnableOutcome(
            reason="written",
            provider_written=provider,
            detail=f"wrote provider={provider!r} to vox.md",
        )

    def _existing_provider(self) -> str | None:
        """Return the existing provider string, or ``None`` if unset/empty."""
        try:
            raw = self._store.read_field("provider")
        except (OSError, ValueError):
            # A malformed vox.md is out of scope for the proposal
            # step -- treat as no declared provider so the caller
            # gets one from the daemon rather than crash mid-enable.
            return None
        if raw is None or not raw.strip():
            return None
        return raw.strip()

    def _ask_daemon(self) -> EnableOutcome:
        """Query voxd for its preferred provider and classify the answer."""
        client = self._client_factory()
        try:
            payload = client.provider_status()
        except _DAEMON_ERRORS as exc:
            return EnableOutcome(
                reason="voxd_unavailable",
                provider_written=None,
                detail=(
                    "voxd is not reachable; no provider was selected — "
                    "start the daemon and run `vox provider <name>` "
                    f"({exc})"
                ),
            )
        preferred = payload.preferred
        if preferred is None:
            return EnableOutcome(
                reason="no_ready_provider",
                provider_written=None,
                detail=(
                    "no provider on this daemon is ready; no provider was "
                    "selected — set at least one credential (e.g. "
                    "ELEVENLABS_API_KEY, OPENAI_API_KEY, or AWS "
                    "credentials) and run `vox provider <name>`"
                ),
            )
        # The write path picks up ``provider_written``; here it is only
        # a claim, not yet in vox.md.
        return EnableOutcome(
            reason="written",
            provider_written=preferred,
            detail="",
        )
