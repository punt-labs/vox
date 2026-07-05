"""An in-memory ``ProgramGateway`` fake shared by the server and CLI surface tests.

The surfaces are thin adapters over :class:`~punt_vox.program_gateway.ProgramGateway`,
so their tests inject this fake instead of a live daemon: it records every call,
returns a caller-controlled :class:`ProgramStatus`, and lets a test flip the
applied/rejected result (design F7) or mutate the status between calls (to prove
the surface re-reads authoritatively -- vox-73m5). It is a structural stand-in
(no inheritance), matching the Protocol by shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, final

from punt_vox.program_control import CommandOutcome, ProgramSummary, StartRequest
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName
from punt_vox.voxd.programs.status import ProgramStatus


@final
@dataclass(slots=True)
class GatewayCall:
    """One recorded call against the fake: the verb and its salient argument."""

    verb: str
    name: str | None = None
    part: int | None = None
    request: StartRequest | None = None


@final
class FakeProgramGateway:
    """A stateful, filesystem-free ``ProgramGateway`` for surface tests."""

    __slots__ = ("_applied", "_catalog", "_status", "calls")
    _status: ProgramStatus
    _catalog: tuple[ProgramSummary, ...]
    _applied: bool
    calls: list[GatewayCall]

    def __new__(
        cls,
        status: ProgramStatus | None = None,
        catalog: tuple[ProgramSummary, ...] = (),
        *,
        applied: bool = True,
    ) -> Self:
        self = super().__new__(cls)
        self._status = status if status is not None else ProgramStatus.idle()
        self._catalog = catalog
        self._applied = applied
        self.calls = []
        return self

    def set_status(self, status: ProgramStatus) -> None:
        """Replace the status a subsequent :meth:`status` call returns."""
        self._status = status

    def _outcome(self, message: str) -> CommandOutcome:
        return CommandOutcome(applied=self._applied, message=message)

    def status(self) -> ProgramStatus:
        self.calls.append(GatewayCall("status"))
        return self._status

    def start(self, request: StartRequest) -> CommandOutcome:
        self.calls.append(GatewayCall("start", request=request))
        return self._outcome("on")

    def stop(self) -> CommandOutcome:
        self.calls.append(GatewayCall("stop"))
        return self._outcome("off")

    def advance(self) -> CommandOutcome:
        self.calls.append(GatewayCall("advance"))
        return self._outcome("advanced")

    def play(self, name: ProgramName, part: PartRef | None) -> CommandOutcome:
        index = None if part is None else part.index
        self.calls.append(GatewayCall("play", name=name.value, part=index))
        return self._outcome(f"playing {name.value}")

    def loop(self, name: ProgramName) -> CommandOutcome:
        self.calls.append(GatewayCall("loop", name=name.value))
        return self._outcome(f"looping {name.value}")

    def catalog(self) -> tuple[ProgramSummary, ...]:
        self.calls.append(GatewayCall("catalog"))
        return self._catalog

    def verbs(self) -> list[str]:
        """Return the recorded call verbs in order (a test-readability helper)."""
        return [call.verb for call in self.calls]
