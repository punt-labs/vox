"""The private file that records the agent and tools Vox provisioned."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Final, Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.private_state import PrivateState
from punt_vox.sibling_lock import SiblingLock
from punt_vox.voxd.voice_agent.contracts import Registration
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.reply import Reply

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

__all__ = ["RegistrationFile"]

_RETIRED: Final = frozenset(
    {"retired_agents", "retired_tools", "pending_agents", "pending_tools"}
)


@final
class RegistrationFile:
    """Read, write and lock one registration file, owned by one ElevenLabs key."""

    __slots__ = ("_credential_id", "_file", "_lock")
    _credential_id: str
    _file: AtomicFile
    _lock: SiblingLock

    def __new__(cls, path: Path, credential_id: str) -> Self:
        """Bind the file and the credential that may use it."""
        self = super().__new__(cls)
        self._credential_id = credential_id
        self._file = AtomicFile(path)
        self._lock = SiblingLock(path)
        return self

    @contextmanager
    def locked(self) -> Generator[None]:
        """Hold the registration lock for provisioning, teardown or a merge."""
        PrivateState(self._file.path).ensure_private_tree()
        with ExitStack() as stack:
            try:
                stack.enter_context(self._lock.held())
            except TimeoutError:
                msg = "ElevenLabs agent provisioning or teardown is in progress; retry"
                raise ConvAIError(msg) from None
            yield

    def read(
        self, *, for_teardown: bool = False
    ) -> Registration | None:  # No file is valid before provisioning.
        """Return the registration, or None before the first provisioning.

        A file written before retirement existed is refused with how to clear
        it. Teardown alone reads it, so the one instruction it gives can be
        followed: it deletes the agent and tools the file names.
        """
        if self._file.path.is_symlink():
            msg = "Vox voice agent config must not be a symbolic link"
            raise ConvAIError(msg)
        try:
            raw = self._file.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"Unreadable Vox voice agent config ({type(exc).__name__})"
            raise ConvAIError(msg) from None
        return self._parse(raw, for_teardown=for_teardown)

    def write(self, record: Registration) -> None:
        """Replace the file atomically, readable by its owner only."""
        self._file.replace(json.dumps(asdict(record)) + "\n", mode=0o600)

    def remove(self) -> None:
        """Delete the file; the lock file beside it stays."""
        self._file.path.unlink()

    def _parse(self, raw: str, *, for_teardown: bool) -> Registration:
        try:
            data: object = json.loads(raw)
        except (ValueError, RecursionError):
            msg = "Unreadable Vox voice agent config"
            raise ConvAIError(msg) from None
        fields = cast("dict[str, object]", data) if isinstance(data, dict) else {}
        match fields:
            case {
                "agent_id": (str() | None) as agent_id,
                "tool_ids": ids,
                "configuration_hash": str() as fingerprint,
                "credential_id": str() as credential_id,
            }:
                if credential_id != self._credential_id:
                    msg = (
                        "Vox voice agent uses a different ElevenLabs key or endpoint; "
                        "restore it and tear down the agent before switching"
                    )
                    raise ConvAIError(msg)
                if not for_teardown and not fields.keys() >= _RETIRED:
                    msg = (
                        "Vox voice agent config is from an older build; "
                        "run `vox call teardown` once"
                    )
                    raise ConvAIError(msg)
                return Registration(
                    None if agent_id is None else Reply.resource_id(agent_id),
                    self._ids(ids),
                    fingerprint,
                    credential_id,
                    self._ids(fields.get("retired_agents", [])),
                    self._ids(fields.get("retired_tools", [])),
                    self._ids(fields.get("pending_agents", [])),
                    self._ids(fields.get("pending_tools", [])),
                )
            case _:
                msg = "Invalid Vox voice agent config"
                raise ConvAIError(msg)

    @staticmethod
    def _ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            msg = "Invalid tool ID list in Vox voice agent config"
            raise ConvAIError(msg)
        return tuple(
            RegistrationFile._validated(item) for item in cast("list[object]", value)
        )

    @staticmethod
    def _validated(value: object) -> str:
        if not isinstance(value, str):
            msg = "Invalid tool ID in Vox voice agent config"
            raise ConvAIError(msg)
        return Reply.resource_id(value)
