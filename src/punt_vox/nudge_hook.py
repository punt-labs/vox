"""The UserPromptSubmit auto-vibe reminder: advance the cadence, persist, trace.

The hook layer is a thin adapter over :class:`NudgeHook`. The hook owns the
cadence read/write and the reminder envelope so the pure :class:`VibeNudge`
decision stays I/O-free, and it emits the stable ``[vibe-trace]`` nudge event a
human greps to prove the auto-vibe link fired.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self, final

from punt_vox.config import ConfigStore, VoxConfig
from punt_vox.vibe_nudge import VibeNudge

__all__ = ["NudgeHook"]

logger = logging.getLogger(__name__)

_TRACE = "[vibe-trace]"


@final
class NudgeHook:
    """Advance the auto-vibe cadence for one prompt and emit the reminder + trace."""

    __slots__ = ("_config_dir", "_nudge")

    _config_dir: Path
    _nudge: VibeNudge

    def __new__(cls, config_dir: Path, nudge: VibeNudge | None = None) -> Self:
        self = super().__new__(cls)
        self._config_dir = config_dir
        self._nudge = nudge if nudge is not None else VibeNudge()
        return self

    def run(self, config: VoxConfig) -> dict[str, object] | None:
        """Return the reminder envelope, or ``None`` to stay silent.

        In ``auto`` the cadence advances and persists; the reminder fires (and the
        ``[vibe-trace]`` nudge event is emitted) every Nth prompt. The reminder is
        coupled to the reset -- a failed persist stays silent so it never re-fires
        on every later prompt. ``None`` is the documented "no nudge" contract
        (PY-TS-14), not a failure to produce a value.
        """
        decision = self._nudge.advance(
            mode=config.vibe_mode, turns=config.vibe_nudge_turns
        )
        if config.vibe_mode == "auto" and not self._persist(decision.next_turns):
            return None
        if decision.reminder is None:
            return None
        logger.info(
            "%s nudge fired counter=%s->%s mode=%s",
            _TRACE,
            config.vibe_nudge_turns + 1,
            decision.next_turns,
            config.vibe_mode,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": decision.reminder,
            }
        }

    def _persist(self, next_turns: int) -> bool:
        """Persist the cadence counter; return ``False`` (and warn) on write failure."""
        try:
            ConfigStore(self._config_dir).write_field(
                "vibe_nudge_turns", str(next_turns)
            )
        except OSError as exc:
            logger.warning(
                "vibe-nudge: cannot persist cadence in %s: %s", self._config_dir, exc
            )
            return False
        return True
