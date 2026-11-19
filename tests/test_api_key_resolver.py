"""Tests for punt_vox.api_key_resolver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer

from punt_vox.api_key_resolver import ApiKeyResolver


class TestApiKeyResolver:
    def test_resolve_from_file(self, tmp_path: Path) -> None:
        """Read a key from a file with proper permissions."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_test_key\n", encoding="utf-8")
        key_path.chmod(0o600)

        ctx = MagicMock(spec=typer.Context)
        resolver = ApiKeyResolver(ctx, None, key_path, api_key_stdin=False)
        assert resolver.resolve() == "sk_test_key"

    def test_resolve_returns_none_when_no_source(self) -> None:
        """No source configured returns None (anonymous)."""
        ctx = MagicMock(spec=typer.Context)
        resolver = ApiKeyResolver(ctx, None, None, api_key_stdin=False)
        assert resolver.resolve() is None

    def test_mutual_exclusion_file_and_stdin(self, tmp_path: Path) -> None:
        """File + stdin together raises BadParameter."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_test\n", encoding="utf-8")
        key_path.chmod(0o600)

        ctx = MagicMock(spec=typer.Context)
        resolver = ApiKeyResolver(ctx, None, key_path, api_key_stdin=True)
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            resolver.resolve()

    def test_mutual_exclusion_file_and_argv(self, tmp_path: Path) -> None:
        """File + argv key together raises BadParameter."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file\n", encoding="utf-8")
        key_path.chmod(0o600)

        ctx = MagicMock(spec=typer.Context)
        resolver = ApiKeyResolver(ctx, "sk_argv", key_path, api_key_stdin=False)
        with pytest.raises(typer.BadParameter, match="mutually exclusive"):
            resolver.resolve()

    def test_empty_env_key_does_not_shadow_file(self, tmp_path: Path) -> None:
        """An empty VOX_API_KEY is no source, so --api-key-file still resolves."""
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file\n", encoding="utf-8")
        key_path.chmod(0o600)

        ctx = MagicMock(spec=typer.Context)
        resolver = ApiKeyResolver(ctx, "", key_path, api_key_stdin=False)
        assert resolver.resolve() == "sk_file"

    def test_read_stdin_rejects_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """stdin reader rejects tty input."""
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        monkeypatch.setattr("punt_vox.api_key_resolver.sys.stdin", fake_stdin)

        with pytest.raises(typer.BadParameter, match="requires piped input"):
            ApiKeyResolver._read_stdin()  # pyright: ignore[reportPrivateUsage]

    def test_read_stdin_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty stdin raises BadParameter."""
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = False
        fake_stdin.readline.return_value = ""
        monkeypatch.setattr("punt_vox.api_key_resolver.sys.stdin", fake_stdin)

        with pytest.raises(typer.BadParameter, match="empty input"):
            ApiKeyResolver._read_stdin()  # pyright: ignore[reportPrivateUsage]
