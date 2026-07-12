"""Tests for punt_vox.voxd.chimes -- ChimeResolver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.voxd.chimes import ChimeResolver


class TestChimeResolver:
    """ChimeResolver maps signal names to bundled asset paths."""

    def test_known_signal_returns_path(self) -> None:
        resolver = ChimeResolver()
        path = resolver.resolve("done")
        # The asset may or may not exist in the test environment,
        # but the resolver should return a Path when the file exists
        # or None when it doesn't. We verify the logic by mocking.
        assert path is None or isinstance(path, Path)

    def test_unknown_signal_returns_none(self) -> None:
        resolver = ChimeResolver()
        assert resolver.resolve("nonexistent-signal") is None

    def test_all_chime_map_entries_resolve_consistently(self) -> None:
        resolver = ChimeResolver()
        for signal in ChimeResolver._CHIME_MAP:
            result = resolver.resolve(signal)
            assert result is None or isinstance(result, Path)

    def test_resolve_returns_path_when_asset_exists(self) -> None:
        resolver = ChimeResolver()
        fake_path = Path("/tmp/chime_done.mp3")
        with (
            patch("punt_vox.voxd.chimes.importlib.resources.files") as mock_files,
            patch.object(Path, "exists", return_value=True),
        ):
            mock_files.return_value.joinpath.return_value = fake_path
            result = resolver.resolve("done")
        assert result == fake_path

    def test_resolve_returns_none_when_asset_missing(self) -> None:
        resolver = ChimeResolver()
        with (
            patch("punt_vox.voxd.chimes.importlib.resources.files") as mock_files,
            patch.object(Path, "exists", return_value=False),
        ):
            mock_files.return_value.joinpath.return_value = Path("/missing.mp3")
            result = resolver.resolve("done")
        assert result is None

    def test_resolve_handles_file_not_found_error(self) -> None:
        resolver = ChimeResolver()
        with patch(
            "punt_vox.voxd.chimes.importlib.resources.files",
            side_effect=FileNotFoundError,
        ):
            assert resolver.resolve("done") is None

    def test_resolve_handles_type_error(self) -> None:
        resolver = ChimeResolver()
        with patch(
            "punt_vox.voxd.chimes.importlib.resources.files",
            side_effect=TypeError,
        ):
            assert resolver.resolve("done") is None

    def test_chime_map_is_class_variable(self) -> None:
        """_CHIME_MAP is shared across instances, not per-instance."""
        a = ChimeResolver()
        b = ChimeResolver()
        assert a._CHIME_MAP is b._CHIME_MAP

    def test_chime_map_has_expected_signals(self) -> None:
        expected = {
            "done",
            "prompt",
            "acknowledge",
            "compact",
            "subagent",
            "farewell",
            "tests-fail",
            "lint-fail",
        }
        assert set(ChimeResolver._CHIME_MAP.keys()) == expected
