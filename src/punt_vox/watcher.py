"""Session event watcher for real-time transcript monitoring.

Daemon thread that tails the active Claude Code session JSONL file,
classifies bash tool output by pattern matching, and dispatches
events to registered consumers. The first consumer announces
milestones (tests passed, lint clean, code pushed) via speech or
chime when ``notify=c``.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from punt_vox.chime import resolve_chime_path
from punt_vox.config import read_config
from punt_vox.playback import enqueue as _enqueue_audio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionEvent:
    """A classified event from the session transcript."""

    signal: str
    timestamp: float
    source_text: str


SessionEventConsumer = Callable[[SessionEvent], None]


# ---------------------------------------------------------------------------
# Classification — delegates to hooks.classify_signal (single source of truth)
# ---------------------------------------------------------------------------


def classify_output(text: str) -> str | None:
    """Classify bash output into a signal name, or None if unrecognized.

    Delegates to :func:`punt_vox.hooks.classify_signal` which owns the
    canonical pattern table.  Passes ``exit_code=None`` since the watcher
    only has text, not exit codes.
    """
    from punt_vox.hooks import classify_signal

    return classify_signal(exit_code=None, stdout=text)


# ---------------------------------------------------------------------------
# JSONL extraction
# ---------------------------------------------------------------------------


def _extract_tool_result_text(data: dict[str, object]) -> str | None:
    """Extract text content from a tool_result JSONL line.

    Handles multiple conversation formats:
    - ``{"type": "tool_result", "content": "..."}``
    - ``{"type": "tool_result", "content": [{"type": "text", "text": "..."}]}``
    - ``{"role": "user", "content": [{"type": "tool_result", "content": "..."}]}``
    """
    if data.get("type") == "tool_result":
        return _content_to_text(data.get("content"))

    content = data.get("content")
    if not isinstance(content, list):
        return None

    blocks = cast("list[object]", content)
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_dict = cast("dict[str, object]", block)
        if block_dict.get("type") == "tool_result":
            text = _content_to_text(block_dict.get("content"))
            if text:
                parts.append(text)

    return "\n".join(parts) if parts else None


def _content_to_text(content: object) -> str | None:
    """Convert tool_result content (string or structured) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        items = cast("list[object]", content)
        texts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_dict = cast("dict[str, object]", item)
            if item_dict.get("type") == "text":
                t = item_dict.get("text")
                if isinstance(t, str):
                    texts.append(t)
        return "\n".join(texts) if texts else None
    return None


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def derive_session_dir(cwd: Path | None = None) -> Path:
    """Derive the Claude Code session directory for the given cwd.

    Claude Code stores session JSONL files under
    ``~/.claude/projects/{cwd-with-slashes-as-dashes}/``.
    """
    if cwd is None:
        cwd = Path.cwd()
    key = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / key


def _find_session_jsonl(session_dir: Path) -> Path | None:
    """Find the most recently modified ``.jsonl`` in *session_dir*.

    Only considers top-level files (not subdirectories).
    Returns None if the directory doesn't exist or has no JSONL files.
    """
    if not session_dir.is_dir():
        return None

    candidates = [
        f for f in session_dir.iterdir() if f.is_file() and f.suffix == ".jsonl"
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda f: f.stat().st_mtime)


# ---------------------------------------------------------------------------
# Signal phrases
# ---------------------------------------------------------------------------

_SIGNAL_PHRASES: dict[str, str] = {
    "tests-pass": "Tests passed.",
    "tests-fail": "Tests failed.",
    "lint-pass": "Lint clean.",
    "lint-fail": "Lint errors found.",
    "git-push-ok": "Code pushed.",
    "merge-conflict": "Merge conflict detected.",
    "git-commit": "Commit complete.",
    "pr-created": "Pull request created.",
}


# ---------------------------------------------------------------------------
# Notification consumer
# ---------------------------------------------------------------------------


def make_notification_consumer(
    config_dir: Path | None = None,
    throttle_seconds: float = 15.0,
) -> SessionEventConsumer:
    """Create a consumer that announces events via speech or chime.

    Only active when ``notify=c``. Throttles per-signal-type with a
    minimum interval of *throttle_seconds* between same-signal fires.
    """
    last_fired: dict[str, float] = {}

    def _consumer(event: SessionEvent) -> None:
        config = read_config(config_dir)
        if config.notify != "c":
            return

        now = time.monotonic()
        last = last_fired.get(event.signal)
        if last is not None and now - last < throttle_seconds:
            logger.debug("Throttled %s (%.1fs since last)", event.signal, now - last)
            return
        last_fired[event.signal] = now

        if config.speak == "y":
            _announce_voice(event)
        else:
            _announce_chime(event.signal, config.vibe)

    return _consumer


def _announce_voice(event: SessionEvent) -> None:
    """Synthesize and play a short phrase for the event."""
    phrase = _SIGNAL_PHRASES.get(event.signal, event.signal)
    try:
        from punt_vox.core import TTSClient
        from punt_vox.providers import get_provider
        from punt_vox.types import SynthesisRequest

        provider = get_provider()
        client = TTSClient(provider)
        request = SynthesisRequest(text=phrase, voice=provider.default_voice)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "watcher_announcement.mp3"
            result = client.synthesize(request, output_path)
            _enqueue_audio(result.path)
    except Exception:
        logger.exception("Failed to announce %s via voice", event.signal)


def _announce_chime(signal: str, vibe: str | None = None) -> None:
    """Play the mood-appropriate chime asset for *signal*."""
    from punt_vox.mood import classify_mood

    mood = classify_mood(vibe)
    chime = resolve_chime_path(signal, mood=mood)
    if chime is None:
        logger.debug("No chime found for signal %s (mood=%s)", signal, mood)
        return
    _enqueue_audio(chime)


# ---------------------------------------------------------------------------
# SessionWatcher
# ---------------------------------------------------------------------------


class SessionWatcher:
    """Daemon thread that tails a Claude Code session JSONL file.

    New lines are parsed, classified by pattern matching on bash tool
    output, and dispatched to registered consumer callbacks.
    """

    def __init__(
        self,
        session_dir: Path,
        consumers: list[SessionEventConsumer],
        poll_interval: float = 1.0,
    ) -> None:
        self._session_dir = session_dir
        self._consumers = list(consumers)
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._file_latched = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch the watcher daemon thread."""
        if self._thread is not None:
            if self._thread.is_alive():
                return
            logger.warning(
                "Session watcher thread for %s was not alive; restarting",
                self._session_dir,
            )
            self._thread = None

        self._stop_event.clear()
        self._file_latched.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="tts-session-watcher",
            daemon=True,
        )
        self._thread.start()
        logger.info("Session watcher started for %s", self._session_dir)

    def stop(self) -> None:
        """Signal the watcher thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning(
                    "Session watcher thread for %s did not stop within timeout",
                    self._session_dir,
                )
            else:
                self._thread = None
        logger.info("Session watcher stopped")

    @property
    def running(self) -> bool:
        """Whether the watcher thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        """Main loop: find JSONL, tail it, process new lines."""
        current_path: Path | None = None
        current_inode: int | None = None
        file_pos: int = 0

        while not self._stop_event.is_set():
            try:
                jsonl = _find_session_jsonl(self._session_dir)
                if jsonl is None:
                    logger.debug("No JSONL found in %s", self._session_dir)
                    self._stop_event.wait(self._poll_interval)
                    continue

                try:
                    stat = jsonl.stat()
                except OSError:
                    self._stop_event.wait(self._poll_interval)
                    continue

                # New or rotated file — seek to end
                if jsonl != current_path or stat.st_ino != current_inode:
                    current_path = jsonl
                    current_inode = stat.st_ino
                    file_pos = stat.st_size
                    self._file_latched.set()
                    logger.debug(
                        "Tracking %s (inode=%d, pos=%d)",
                        jsonl,
                        current_inode,
                        file_pos,
                    )
                    self._stop_event.wait(self._poll_interval)
                    continue

                # File truncated (e.g. copytruncate) — reset position
                if stat.st_size < file_pos:
                    logger.debug(
                        "File %s truncated (was %d, now %d), resetting",
                        jsonl,
                        file_pos,
                        stat.st_size,
                    )
                    file_pos = stat.st_size
                    self._stop_event.wait(self._poll_interval)
                    continue

                # Read new lines
                if stat.st_size > file_pos:
                    try:
                        with jsonl.open("r") as f:
                            f.seek(file_pos)
                            new_data = f.read()
                            file_pos = f.tell()
                    except OSError:
                        logger.debug("Failed to read %s", jsonl)
                        self._stop_event.wait(self._poll_interval)
                        continue

                    for raw_line in new_data.splitlines():
                        stripped = raw_line.strip()
                        if stripped:
                            self._process_line(stripped)

                self._stop_event.wait(self._poll_interval)
            except Exception:
                logger.exception("Unhandled error in session watcher loop; continuing")
                self._stop_event.wait(self._poll_interval)

    def _process_line(self, line: str) -> None:
        """Parse a JSONL line, classify, and dispatch to consumers."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return

        if not isinstance(raw, dict):
            return

        data = cast("dict[str, object]", raw)
        text = _extract_tool_result_text(data)
        if not text:
            return

        signal = classify_output(text)
        if signal is None:
            return

        event = SessionEvent(
            signal=signal,
            timestamp=time.time(),
            source_text=text[:200],
        )

        for consumer in self._consumers:
            try:
                consumer(event)
            except Exception:
                logger.exception("Consumer %r failed for %s", consumer, event.signal)
