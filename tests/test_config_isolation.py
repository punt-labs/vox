"""Regression guards proving the suite never touches the real vox config.

The developer's live ``.punt-labs/vox/vox.local.md`` was being overwritten
whenever anyone ran ``make test``: fixtures drove the ``vibe`` MCP tool and the
``/vibe`` CLI command through config-writing paths that resolved
``DEFAULT_CONFIG_DIR`` (a *relative* path) against the repo root.  The autouse
``hermetic_config`` fixture in ``conftest.py`` now redirects every ambient
resolution to a per-test tmp dir.  These tests fail loudly if that redirect ever
regresses.
"""

from __future__ import annotations

from pathlib import Path

import punt_vox.config as config_mod
import punt_vox.dirs as dirs_mod
from punt_vox.config import ConfigStore
from punt_vox.dirs import find_repo_root


def _real_config_dir() -> Path:
    """Return the repo's real ``.punt-labs/vox`` dir, bypassing the redirect."""
    repo_root = find_repo_root()
    assert repo_root is not None, "tests must run inside the vox git repo"
    return repo_root / ".punt-labs" / "vox"


class TestSuiteDoesNotTouchRealConfig:
    """The autouse redirect must be active and total for every test."""

    def test_redirect_is_active(self, hermetic_config: Path) -> None:
        """Default resolution points at the tmp dir, not the relative default."""
        assert hermetic_config == config_mod.DEFAULT_CONFIG_DIR
        assert hermetic_config == dirs_mod.DEFAULT_CONFIG_DIR
        assert hermetic_config == dirs_mod.find_config_dir()
        assert hermetic_config != Path(".punt-labs") / "vox"

    def test_default_write_lands_in_tmp(self, hermetic_config: Path) -> None:
        """A dir-less write reaches the tmp config, not the repo config."""
        ConfigStore().write_field("vibe", "SENTINEL")
        ConfigStore().write_fields({"vibe_mode": "manual", "vibe_tags": "[excited]"})

        local = hermetic_config / "vox.local.md"
        assert local.exists()
        text = local.read_text()
        assert 'vibe: "SENTINEL"' in text
        assert 'vibe_mode: "manual"' in text

    def test_real_config_untouched_by_default_paths(self) -> None:
        """Driving default read/write paths never creates or edits the real config."""
        real_local = _real_config_dir() / "vox.local.md"
        before = real_local.read_bytes() if real_local.exists() else None

        # Exercise the exact paths that leaked: dir-less read + dir-less writes.
        ConfigStore().read()
        ConfigStore().write_field("vibe", "leak-check")
        ConfigStore().write_fields({"vibe": "leak-check-2", "vibe_mode": "off"})

        after = real_local.read_bytes() if real_local.exists() else None
        assert after == before, "default config path wrote to the real vox config"
