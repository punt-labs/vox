"""Tests for the Claude Desktop registration document."""

from pathlib import Path

from punt_vox.desktop_config import DesktopConfig
from punt_vox.output_formatter import OutputFormatter


def test_registration_round_trip(tmp_path: Path) -> None:
    config = DesktopConfig(tmp_path / "claude_desktop_config.json", OutputFormatter())

    data = config.load()
    assert data == {}
    config.server_map(data)["vox"] = {"command": "uvx"}
    config.write(data)

    assert config.load() == {"mcpServers": {"vox": {"command": "uvx"}}}
