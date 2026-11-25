"""Apply shared provider/model/voice cascade rules.

Setting a provider selects its first model and voice; setting a model selects
the current provider's first voice. Setting a voice has no cascade. Empty
rosters yield empty defaults. Each surface translates RosterError into its own
error envelope; roster retrieval and default selection stay shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from websockets.exceptions import WebSocketException

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRejectionError,
)
from punt_vox.models import MODEL_TABLE
from punt_vox.voice_target import VoiceTarget

if TYPE_CHECKING:
    from collections.abc import Sequence

    from punt_vox.voice_target import RosterClient

__all__ = ["Cascade", "RosterError", "RosterRejectedError"]

logger = logging.getLogger(__name__)


# Non-connect, non-rejection faults on the voxd voices() wire funnel to the
# same typed sentinel. VoxdConnectionError (the daemon is down) is common
# and prosaic; VoxdRejectionError (the daemon said no with a reason) is
# handled separately so the caller can render the reason; the others
# (bare protocol errors, websocket faults, OS errors, ValueError from a
# malformed response) log with .exception() before wrapping so a real bug
# leaves a diagnostic trail.
_VOICES_FAULT_ERRORS = (VoxdProtocolError, WebSocketException, OSError, ValueError)


@dataclass(frozen=True, slots=True)
class RosterError:
    """A daemon fault on the roster fetch, wrapped as a typed sentinel.

    A plain ``str`` return would collide with a real voice name (empty
    string ``""`` is a valid modelless-roster answer). Wrapping the failure
    in a distinct type lets the caller ``isinstance``-branch cleanly
    instead of comparing against magic values.

    The concrete subtype :class:`RosterRejectedError` narrows a wire-level
    rejection (voxd was reached and answered with a typed error frame)
    apart from the base case (voxd was unreachable or malformed) so a
    caller can render voxd's reason verbatim rather than a generic
    "unavailable" line that hides why the roster is missing.
    """

    message: str


@final
@dataclass(frozen=True, slots=True)
class RosterRejectedError(RosterError):
    """A ``{"type": "error"}`` frame surfaced during the roster fetch.

    Distinct from the base :class:`RosterError` (an unreachable daemon,
    a malformed reply): voxd was reached and said no with a caller-facing
    ``message``. A surface may render that message verbatim so the operator
    sees the daemon's reason rather than a generic "unavailable" hint.
    """


@final
class Cascade:
    """Namespace for the switch-cascade helpers.

    A class rather than free functions because the OO ratchet
    (``method_ratio`` / ``class_to_func_ratio``) treats a module of free
    helpers as a procedural leak; grouping them on one type keeps the
    stance consistent while the members stay stateless.
    """

    __slots__ = ()

    @staticmethod
    def first_or_empty(seq: Sequence[str]) -> str:
        """Return the first element of *seq*, or ``""`` when *seq* is empty.

        Deterministic "first from list" default per the cascade contract.
        Used for both the model default
        (``MODEL_TABLE.available(provider)[0]``) and the voice default
        (``client.voices(target)[0]``).
        """
        return seq[0] if seq else ""

    @staticmethod
    def default_target(provider: str) -> VoiceTarget:
        """Select the first model in *provider*'s list, if it has one.

        A modelless provider (say, espeak, polly) has an empty
        :data:`MODEL_TABLE.available` list; the cascade rule writes ``""``
        in that case, which the frontmatter store treats as absent.
        """
        model = next(iter(MODEL_TABLE.available(provider)), None)
        return VoiceTarget(provider, model)

    @staticmethod
    def fetch_roster(
        client: RosterClient, target: VoiceTarget
    ) -> list[str] | RosterError:
        """Return the target's voice roster, or a :class:`RosterError`.

        The target always names a provider: ``mic:model``, ``mic:voice``, and
        the panel each refuse when their session has no provider yet
        rather than pass ``None`` and let the daemon substitute.

        A :class:`VoxdConnectionError` (daemon down) is wrapped without
        logging; a :class:`VoxdProtocolError`, :class:`WebSocketException`,
        :class:`OSError`, or :class:`ValueError` is logged with
        ``.exception()`` before wrapping so a real bug leaves a diagnostic
        trail. Callers translate the wrapper into their own per-surface
        error envelope.
        """
        try:
            return client.voices(target)
        except VoxdRejectionError as exc:
            return RosterRejectedError(str(exc))
        except VoxdConnectionError as exc:
            return RosterError(str(exc))
        except _VOICES_FAULT_ERRORS as exc:
            logger.exception("Voice roster fetch failed for %r", target)
            return RosterError(str(exc))

    @staticmethod
    def fetch_first_voice(
        client: RosterClient, target: VoiceTarget
    ) -> str | RosterError:
        """Return the provider's first voice, or a :class:`RosterError` on fault.

        Thin wrapper: :meth:`fetch_roster` then :meth:`first_or_empty`. The
        empty-string return for an empty roster matches the cascade rule --
        the frontmatter store treats ``""`` as absent.
        """
        roster = Cascade.fetch_roster(client, target)
        if isinstance(roster, RosterError):
            return roster
        return Cascade.first_or_empty(roster)
