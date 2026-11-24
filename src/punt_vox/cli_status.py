"""Present the top-level vox status from local config and daemon health."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from punt_vox.cli_daemon import DaemonCli
from punt_vox.cli_worker_repos import WorkerReposStatus
from punt_vox.desktop_registration import DesktopRegistration
from punt_vox.service.daemon_status import DaemonState

if TYPE_CHECKING:
    from punt_vox.config import VoxConfig
    from punt_vox.output_formatter import OutputFormatter

_PROVIDER_DISPLAY = {
    "elevenlabs": "ElevenLabs",
    "polly": "Polly",
    "openai": "OpenAI",
    "say": "Say",
    "espeak": "eSpeak",
}

_LOG_UNKNOWN_REASON = {
    DaemonState.RUNNING: "daemon did not report a level",
    DaemonState.NOT_RUNNING: "daemon not reachable",
}


@final
class StatusCli:
    """Report repo settings alongside the running daemon's state."""

    @classmethod
    def emit(cls, formatter: OutputFormatter, cfg: VoxConfig) -> None:
        """Emit the top-level CLI's current status."""
        daemon = DaemonCli.probe_status()
        health = daemon.health
        log_level = health.log_level if health is not None else "unknown"
        workers = WorkerReposStatus(daemon.state)
        info: dict[str, str | list[str] | None] = {
            "daemon": daemon.state.value.replace("_", " "),
            "provider": cfg.provider or "unknown",
            "voice": cfg.voice or None,
            "notify": cfg.notify,
            "speak": cfg.speak,
            "vibe_mode": cfg.vibe_mode,
            "vibe": cfg.vibe,
            "vibe_tags": cfg.vibe_tags,
            "log_level": log_level,
            "desktop": DesktopRegistration.read(),
            **workers.fields(),
        }
        label = cls._log_label(log_level, daemon.state)
        formatter.emit(info, cls._text(info, label, workers.label()))

    @staticmethod
    def _log_label(level: str, state: DaemonState) -> str:
        if level != "unknown":
            return level
        reason = _LOG_UNKNOWN_REASON.get(state, "daemon health unavailable")
        return f"unknown ({reason})"

    @staticmethod
    def _text(
        info: dict[str, str | list[str] | None], log_level: str, workers: str
    ) -> str:
        provider = str(info["provider"] or "unknown")
        lines = [
            f"Daemon:    {info['daemon']}",
            f"Provider:  {_PROVIDER_DISPLAY.get(provider, provider)}",
            f"Voice:     {info['voice'] or '(default)'}",
            f"Notify:    {info['notify']}",
            f"Speak:     {info['speak']}",
            f"Vibe mode: {info['vibe_mode']}",
            f"Log level: {log_level}",
            f"Desktop:   {info['desktop']}",
            f"Workers:   {workers}",
        ]
        if info["vibe"]:
            lines.append(f"Vibe:      {info['vibe']}")
        if info["vibe_tags"]:
            lines.append(f"Tags:      {info['vibe_tags']}")
        return "\n".join(lines)
