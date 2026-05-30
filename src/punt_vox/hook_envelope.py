"""The common hook-stdin envelope: the session ``cwd`` shared by every event."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HookEnvelope:
    """The fields every Claude Code hook payload shares on stdin.

    Today that is just ``cwd`` — the session's working directory.
    Continuous-mode events (PreCompact, UserPromptSubmit,
    SubagentStart/Stop, SessionEnd) carry no event-specific fields the
    hooks use, so they parse straight into an envelope to obtain the
    cwd that resolves the session's repo config.
    """

    cwd: Path | None  # PY-TS-14: absent for pre-field / malformed payloads

    @classmethod
    def parse(cls, data: dict[str, object]) -> HookEnvelope:
        """Return the common envelope extracted from raw hook data."""
        return cls(cwd=cls.cwd_of(data))

    @staticmethod
    def cwd_of(data: dict[str, object]) -> Path | None:
        """Return the session cwd from raw hook data, or None when absent.

        Claude Code puts the session's working directory on every hook
        stdin payload as a top-level ``cwd`` string.  ``None`` is the
        documented contract (PY-TS-14): a payload that predates the
        field or arrives malformed carries no cwd, and the caller
        treats that as unconfigured rather than guessing.
        """
        raw = data.get("cwd")
        if isinstance(raw, str) and raw:
            return Path(raw)
        return None


__all__ = ["HookEnvelope"]
