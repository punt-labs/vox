"""Bind worker answers to the prompt the person actually heard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.voice_call.worker_directory import WorkerDirectory

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import WorkerControl
    from punt_vox.voxd.workers.steering_types import DecisionChoice


@final
class SteeringTool:
    """Resolve spoken identities and distinguish retries from new answers."""

    __slots__ = ("_directory", "_workers")
    _directory: WorkerDirectory
    _workers: WorkerControl

    def __new__(cls, workers: WorkerControl, directory: WorkerDirectory) -> Self:
        """Bind worker delivery and the shared canonical worker directory."""
        self = super().__new__(cls)
        self._workers = workers
        self._directory = directory
        return self

    async def resolve(self, arguments: dict[str, object]) -> dict[str, object]:
        """Validate before caching; a choice targets its announced sequence."""
        words = self._text(arguments, "words")
        urgent = arguments.get("urgent", False)
        if not isinstance(urgent, bool):
            raise ValueError("urgent must be a boolean")
        choice = arguments.get("choice")
        if choice is not None and choice not in ("approve", "deny"):
            raise ValueError("choice must be approve or deny")
        if choice is not None and urgent:
            raise ValueError(
                "A decision choice must not interrupt the permission dialog"
            )
        worker = await self._directory.find(self._text(arguments, "worker_id"))
        epoch = self._epoch(arguments, worker["last_report"], choice)
        return {
            "worker_id": worker["id"],
            "words": words,
            "urgent": urgent,
            "choice": choice,
            "prompt_epoch": epoch,
            "prompt_seq": epoch if choice is not None else None,
        }

    @staticmethod
    def _epoch(
        arguments: dict[str, object], report: dict[str, object], choice: object
    ) -> int:
        epoch = (
            arguments.get("prompt_seq")
            if choice is not None
            else report.get("prompt_epoch", 0)
        )
        minimum = 1 if choice is not None else 0
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < minimum:
            raise ValueError(
                "choice requires prompt_seq from the permission prompt you asked about"
                if choice is not None
                else "worker prompt epoch must be a non-negative integer"
            )
        return epoch

    @staticmethod
    def identity(arguments: dict[str, object]) -> dict[str, object]:
        """Decision retries do not depend on a re-transcribed spoken answer."""
        fields = (
            ("worker_id", "choice", "prompt_seq")
            if arguments["choice"] is not None
            else ("worker_id", "words", "urgent", "prompt_epoch")
        )
        return {field: arguments[field] for field in fields}

    async def send(self, arguments: dict[str, object]) -> object:
        """Deliver the validated request through the provider-neutral boundary."""
        return await self._workers.steer(
            cast("str", arguments["worker_id"]),
            cast("str", arguments["words"]),
            urgent=cast("bool", arguments["urgent"]),
            choice=cast("DecisionChoice | None", arguments["choice"]),
            prompt_epoch=cast("int", arguments["prompt_epoch"]),
            prompt_seq=cast("int | None", arguments["prompt_seq"]),
        )

    @staticmethod
    def _text(arguments: dict[str, object], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value
