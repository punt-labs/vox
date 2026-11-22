"""The outcome of one file playback, and how it crosses the wire."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from punt_vox.voxd.data_root_boundary import relativize_to_data_root
from punt_vox.voxd.wire_text import SafeText

__all__ = [
    "MAX_STDERR_LEN",
    "SUSPICIOUS_ELAPSED_S",
    "PlaybackResult",
]

logger = logging.getLogger(__name__)

# Playback under 50ms is a "success" that almost certainly played nothing.
SUSPICIOUS_ELAPSED_S = 0.05

# Cap on the stderr blob we keep per playback. ffplay without -loglevel
# quiet can emit kilobytes of progress lines; we want enough for triage
# without unbounded growth in memory or log files.
MAX_STDERR_LEN = 2000


@dataclass(frozen=True, slots=True)
class PlaybackResult:
    """Outcome of a single audio playback."""

    path: Path
    rc: int
    elapsed_s: float
    stderr: str
    ts: float

    @staticmethod
    def truncate_stderr(text: str) -> str:
        """Return ``text`` clipped to ``MAX_STDERR_LEN`` with head + tail kept."""
        if len(text) <= MAX_STDERR_LEN:
            return text
        half = MAX_STDERR_LEN // 2
        dropped = len(text) - MAX_STDERR_LEN
        return f"{text[:half]}\n... [truncated {dropped} bytes] ...\n{text[-half:]}"

    @property
    def ok(self) -> bool:
        """True when the player exited cleanly and actually played something.

        A clean exit that lasted under ``SUSPICIOUS_ELAPSED_S`` almost
        certainly played nothing (missing audio backend, silently dropped
        output), so it is treated as a failure for the user-facing ``play``
        command -- the same signal :meth:`log_outcome` warns on.
        """
        return self.rc == 0 and self.elapsed_s >= SUSPICIOUS_ELAPSED_S

    def failure_detail(self) -> str:
        """Raw one-line failure reason (absolute paths and all) for the log."""
        if self.rc != 0:
            base = f"player exited rc={self.rc}"
        else:
            base = f"played nothing (elapsed {self.elapsed_s:.3f}s)"
        return f"{base}: {self.stderr}" if self.stderr else base

    def wire_failure_detail(self) -> str:
        """Prefix-free failure reason for the client frame.

        The raw :meth:`failure_detail` with its host paths stripped: the rc and
        elapsed verdict carry no path, so they pass through, while an in-jail
        stderr token relativizes and an out-of-jail one strips via
        :class:`SafeText`. The client learns why playback failed without seeing
        an absolute prefix; the raw text stays in the host-local log.
        """
        return SafeText.of(self.failure_detail()).text

    def to_health_dict(self) -> dict[str, object]:
        """Serialize for the authenticated health payload with no host paths.

        ``file`` is an in-jail recording or album path, so it crosses relativized
        to its data root (``recordings/foo.mp3``) -- or ``None`` if it resolves
        out of jail. ``stderr`` echoes absolute player paths, so its in-jail
        tokens relativize and its out-of-jail ones strip via :class:`SafeText`.
        ``rc``, ``elapsed_s``, and ``ts`` are service verdicts and cross as-is.
        """
        rel = relativize_to_data_root(self.path)
        return {
            "file": None if rel is None else str(rel.path),
            "rc": self.rc,
            "elapsed_s": self.elapsed_s,
            "stderr": SafeText.of(self.stderr).text,
            "ts": self.ts,
        }

    def log_outcome(
        self, *, size: int, cmd: list[str], env_snapshot: dict[str, str]
    ) -> None:
        """Interpret this result and log at the appropriate level.

        The result bundles the file, exit code, elapsed time and stderr; the
        caller adds the spawn context it does not carry (``size``, ``cmd``,
        ``env_snapshot``), so the log line reproduces the exact invocation
        for triage.
        """
        name = self.path.name
        if self.rc != 0:
            logger.error(
                "Playback FAILED: rc=%d elapsed=%.3fs file=%s size=%d "
                "cmd=%s audio_env=%s stderr=%r",
                self.rc,
                self.elapsed_s,
                name,
                size,
                cmd,
                env_snapshot,
                self.stderr,
            )
            return

        if self.elapsed_s < SUSPICIOUS_ELAPSED_S:
            logger.warning(
                "Playback SUSPICIOUS: rc=0 but elapsed=%.4fs (<%.2fs) file=%s "
                "size=%d audio_env=%s stderr=%r -- probably played nothing",
                self.elapsed_s,
                SUSPICIOUS_ELAPSED_S,
                name,
                size,
                env_snapshot,
                self.stderr,
            )
            return

        logger.debug(
            "Playback ok: elapsed=%.3fs file=%s size=%d stderr=%r",
            self.elapsed_s,
            name,
            size,
            self.stderr,
        )
