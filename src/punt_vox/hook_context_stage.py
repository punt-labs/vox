"""Prepare redacted hook events that fit the daemon's WebSocket frame cap."""

from __future__ import annotations

import fcntl
import json
import secrets
from typing import TYPE_CHECKING, cast

from punt_vox.atomic_file import AtomicFile
from punt_vox.hook_context_event import HookRedaction
from punt_vox.voxd.hook_context_projection import (
    HOOK_TOOL_EVENTS,
    HookText,
    HookToolSubject,
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.hook_context_event import HookIdentity

_HOOK_PAYLOAD_BUDGET = 240 * 1024


class HookContextStage:
    """Keep raw values out of staged events while preserving retry identity."""

    @classmethod
    def prepare(
        cls, identity: HookIdentity, payload: dict[str, object], sequence_dir: Path
    ) -> dict[str, object]:
        """Redact recursively and project oversized hooks before delivery."""
        staged = HookRedaction.clean(payload)
        staged.pop("relay_tool_subject", None)
        if identity.event in HOOK_TOOL_EVENTS:
            key = None
            if payload.get("tool_input") != staged.get("tool_input"):
                key = cls._key(sequence_dir)
            staged["relay_tool_subject"] = HookToolSubject.fingerprint(payload, key=key)
        if (
            len(json.dumps(staged, ensure_ascii=True, allow_nan=False))
            <= _HOOK_PAYLOAD_BUDGET
        ):
            return staged
        return cls._compact(identity, staged)

    @staticmethod
    def _key(directory: Path) -> bytes:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / ".subject-key"
        with (directory / ".subject-key.lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                key = bytes.fromhex(path.read_text(encoding="ascii"))
            except FileNotFoundError:
                key = secrets.token_bytes(32)
                AtomicFile(path).replace(key.hex(), mode=0o600)
            if len(key) != 32:
                msg = "invalid hook tool subject key"
                raise ValueError(msg)
            return key

    @classmethod
    def _compact(
        cls, identity: HookIdentity, staged: dict[str, object]
    ) -> dict[str, object]:
        compact: dict[str, object] = {
            "session_id": identity.session,
            "hook_event_name": identity.event,
        }
        cwd = staged.get("cwd")
        if isinstance(cwd, str):
            compact["cwd"] = cwd
        if identity.event in HOOK_TOOL_EVENTS:
            compact.update(cls._tool_fields(staged))
        elif identity.event == "UserPromptSubmit":
            compact["prompt"] = HookText.clip(staged.get("prompt"))
        elif identity.event == "Stop":
            compact["last_assistant_message"] = HookText.clip(
                staged.get("last_assistant_message")
            )
        return compact

    @staticmethod
    def _tool_fields(staged: dict[str, object]) -> dict[str, object]:
        raw = staged.get("tool_input")
        tool_input = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
        return {
            "relay_tool_subject": staged["relay_tool_subject"],
            "tool_name": HookText.clip(staged.get("tool_name")),
            "tool_input": {"file_path": HookText.clip(tool_input.get("file_path"))},
            "tool_response": HookText.result(staged.get("tool_response")),
            "error": HookText.clip(staged.get("error"), tail=True),
        }
