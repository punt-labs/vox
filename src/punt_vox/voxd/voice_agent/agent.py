"""Keep one persistent ElevenLabs agent and its owned tool IDs in daemon config."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.paths import config_dir
from punt_vox.private_state import PrivateState
from punt_vox.sibling_lock import SiblingLock
from punt_vox.voxd.voice_agent.control import ConvAIControl
from punt_vox.voxd.voice_agent.fault import ConvAIError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from punt_vox.voxd.voice_agent.control import ClientTool

__all__ = ["VoiceAgent"]


@dataclass(frozen=True, slots=True)
class _Registration:
    agent_id: str | None
    tool_ids: tuple[str, ...]
    configuration_hash: str
    credential_id: str


@final
class VoiceAgent:
    """Provision lazily and reuse one account-bound agent across conversations.

    Call the blocking control operations off the daemon's event loop. Register
    a stable tool catalog; explicitly tear the agent down to replace that catalog.
    """

    __slots__ = ("_control", "_file", "_lock")
    _control: ConvAIControl
    _file: AtomicFile
    _lock: SiblingLock

    def __new__(cls, control: ConvAIControl, *, path: Path | None = None) -> Self:
        """Bind the agent to the per-user daemon config or a separate test path."""
        self = super().__new__(cls)
        self._control = control
        self._file = AtomicFile(
            path if path is not None else config_dir() / "voice-agent.json"
        )
        self._lock = SiblingLock(self._file.path)
        return self

    def connection(self, tools: tuple[ClientTool, ...]) -> tuple[str, tuple[str, ...]]:
        """Return a fresh signed URL and the registered IDs for the handshake."""
        ordered = tuple(sorted(tools, key=lambda tool: tool.name))
        fingerprint = self._control.registration_fingerprint(ordered)
        with self._locked():
            stored = self._read()
            if stored is not None and stored.agent_id is None:
                self._delete(stored)
                stored = None
            if stored is None:
                stored = self._provision(ordered, fingerprint)
            if stored.configuration_hash != fingerprint:
                msg = "Vox voice agent configuration changed; tear down the agent first"
                raise ConvAIError(msg)
            agent_id = stored.agent_id
            if agent_id is None:
                msg = "No provisioned ElevenLabs agent"
                raise ConvAIError(msg)
        return self._control.signed_url(agent_id), stored.tool_ids

    def teardown(self) -> None:
        """Delete managed resources; retain the config if deletion must be retried."""
        with self._locked():
            if (stored := self._read()) is not None:
                self._delete(stored)

    @contextmanager
    def _locked(self) -> Generator[None]:
        PrivateState(self._file.path).ensure_private_tree()
        with ExitStack() as stack:
            try:
                stack.enter_context(self._lock.held())
            except TimeoutError:
                msg = "ElevenLabs agent provisioning or teardown is in progress; retry"
                raise ConvAIError(msg) from None
            yield

    def _provision(
        self, tools: tuple[ClientTool, ...], fingerprint: str
    ) -> _Registration:
        record = _Registration(None, (), fingerprint, self._control.credential_id)
        self._write(record)
        for tool in tools:
            tool_id = self._control.create_tool(tool)
            record = replace(record, tool_ids=(*record.tool_ids, tool_id))
            self._checkpoint(record, tool_id, "tool", self._control.delete_tool)
        agent_id = self._control.create_agent(record.tool_ids)
        record = replace(record, agent_id=agent_id)
        self._checkpoint(record, agent_id, "agent", self._control.delete_agent)
        return record

    def _checkpoint(
        self,
        record: _Registration,
        resource_id: str,
        kind: str,
        delete: Callable[[str], None],
    ) -> None:
        try:
            self._write(record)
        except OSError as write_error:
            try:
                delete(resource_id)
            except ConvAIError as delete_error:
                msg = (
                    f"Could not record or delete the ElevenLabs {kind} {resource_id}; "
                    "delete it manually in the workspace used to provision it"
                )
                raise ConvAIError(msg) from ExceptionGroup(
                    msg, [write_error, delete_error]
                )
            raise

    def _delete(self, stored: _Registration) -> None:
        if stored.agent_id is not None:
            self._control.delete_agent(stored.agent_id)
            self._write(replace(stored, agent_id=None))
        for tool_id in stored.tool_ids:
            self._control.delete_tool(tool_id)
        self._file.path.unlink()

    def _read(self) -> _Registration | None:
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
        return self._parse_registration(raw)

    def _parse_registration(self, raw: str) -> _Registration:
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
                if credential_id != self._control.credential_id:
                    msg = (
                        "Vox voice agent uses a different ElevenLabs key or endpoint; "
                        "restore it and tear down the agent before switching"
                    )
                    raise ConvAIError(msg)
                return _Registration(
                    None if agent_id is None else ConvAIControl.resource_id(agent_id),
                    self._tool_ids(ids),
                    fingerprint,
                    credential_id,
                )
            case _:
                msg = "Invalid Vox voice agent config"
                raise ConvAIError(msg)

    def _write(self, record: _Registration) -> None:
        self._file.replace(json.dumps(asdict(record)) + "\n", mode=0o600)

    @staticmethod
    def _tool_ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            msg = "Invalid tool ID list in Vox voice agent config"
            raise ConvAIError(msg)
        return tuple(
            VoiceAgent._validated_id(item) for item in cast("list[object]", value)
        )

    @staticmethod
    def _validated_id(value: object) -> str:
        if not isinstance(value, str):
            msg = "Invalid tool ID in Vox voice agent config"
            raise ConvAIError(msg)
        return ConvAIControl.resource_id(value)
