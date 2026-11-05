"""Cross-process control signals for a running ``vox call start`` loop.

``vox call start`` runs the call's loop in the foreground of the process
that started it (FR-2's explicit-hangup and bounded-timeout paths handle
ending it from *within* that process). ``vox call stop`` runs as a
separate, short-lived invocation -- this file is how it reaches the running
loop: one request written, read and cleared by the loop the next time it
checks between turns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_repo_root
from punt_vox.private_state import PrivateState
from punt_vox.types_programs.wire import JsonObject

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from punt_vox.voxd.conversation_mode.call_session import CallSession, SpeakFn

logger = logging.getLogger(__name__)

__all__ = ["ApplyControlFn", "CallControl", "ControlRequest"]

type ControlKind = Literal["stop"]

# Consumes one pending request and returns whether the drive loop should
# stop. Shared by the live and scripted drivers (call_live_driver.py,
# call_scripted.py) as a collaborator, so both handle a request identically;
# *speak* is the call's own gated channel, for a request that must answer
# aloud.
type ApplyControlFn = Callable[["CallControl", CallSession, "SpeakFn"], Awaitable[bool]]


@final
@dataclass(frozen=True, slots=True)
class ControlRequest:
    """One pending cross-process request: hang up."""

    kind: ControlKind


@final
class CallControl:
    """A one-slot mailbox for :class:`ControlRequest`, backed by a JSON file."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @classmethod
    def for_repo(cls) -> Self:
        """Build a :class:`CallControl` at this repo's ``call.control`` path.

        Mirrors :meth:`~.call_lock.CallLock.for_repo` and
        :meth:`~punt_vox.session_spec.SessionSpec.for_repo` -- one place
        this construction is written, instead of every ``vox call`` verb
        rebuilding the same path independently.
        """
        root = find_repo_root() or Path.cwd()
        return cls(root / DEFAULT_CONFIG_DIR / "call" / "call.control")

    def request_stop(self) -> None:
        """Ask the running call to hang up."""
        self._write(ControlRequest(kind="stop"))

    def consume(self) -> ControlRequest | None:
        """Return and clear the pending request, or ``None`` if there is none.

        Claim-and-remove is one atomic operation: ``os.replace`` renames the
        mailbox file to a ``.consuming`` sibling before anything reads it.
        A read-then-unlink sequence has a gap between the two steps in
        which a fresh ``/call stop`` written into that gap would be deleted
        unread -- the hang-up would silently do nothing. Renaming first
        means there is no such gap: any request written after the rename
        starts a new mailbox file untouched by this call.

        A corrupt or unparseable request (a racing partial write, a
        hand-edited file) is still cleared from the mailbox -- logged and
        reported as "no request" rather than raised, since a caller
        mid-call must not have its boundary handler end the whole call
        over a malformed control file.
        """
        consuming_path = self._path.with_name(self._path.name + ".consuming")
        try:
            self._path.replace(consuming_path)
        except FileNotFoundError:
            return None
        try:
            try:
                # UnicodeDecodeError (from AtomicFile.read's utf-8 decode, on
                # a hand-edited or partially-overwritten file with invalid
                # bytes) is a ValueError subclass, same family as
                # JsonObject's own raises -- both, plus OSError (a
                # permissions or disk I/O failure on the read), mean "this
                # mailbox entry is not usable", not "the call ended". Read
                # and parse share one try so any of these hits the same
                # discard path, instead of propagating to the call-ending
                # boundary handler this method exists to spare.
                raw = AtomicFile(consuming_path).read()
                if not raw:
                    return None
                obj = JsonObject.parse(raw, "call.control")
                kind = obj.require_str("kind")
                # Validated, not trusted: a malformed mailbox file (a
                # racing partial write, a hand-edited entry) with an
                # unrecognized "kind" must land on the same discard-and-log
                # path as every other unusable entry, never be constructed
                # into a request call.py's _apply_control would ignore
                # silently. Branching on the literal value (rather than a
                # runtime "not in" check plus a cast) is also what lets
                # both mypy and pyright narrow ``kind`` to the ControlKind
                # literal with no suppression needed.
                if kind == "stop":
                    return ControlRequest(kind="stop")
                msg = f"unrecognized control kind {kind!r}"
                raise ValueError(msg)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "call.control request is unreadable, discarding: %s", exc
                )
                return None
        finally:
            consuming_path.unlink(missing_ok=True)

    def _write(self, request: ControlRequest) -> None:
        # AtomicFile.replace, not a bare write_text: consume() polls this
        # file between turns, and a truncate-then-write leaves a window in
        # which it could read a partial, unparseable file. 0o600 under a
        # 0o700 dir, not AtomicFile's default (0o644 world-readable): the
        # mailbox steers a live call, so no other local user may read or
        # race it.
        payload = {"kind": request.kind}
        PrivateState(self._path).ensure_private_tree()
        AtomicFile(self._path).replace(json.dumps(payload), mode=0o600)
