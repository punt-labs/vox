"""Tests for punt_vox.__main__ (typer CLI)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from punt_vox.__main__ import app

if TYPE_CHECKING:
    from click.testing import Result


_CLI = "punt_vox.__main__"


# ---------------------------------------------------------------------------
# unmute tests
# ---------------------------------------------------------------------------


class TestUnmuteCommand:
    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_basic(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc123")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()
        call_kwargs = mock_instance.synthesize.call_args
        assert call_kwargs[0][0] == "hello"

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_custom_voice(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc123")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "Hallo", "--voice", "hans"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args
        assert call_kwargs[1]["voice"] == "hans"

    def test_unmute_no_text_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["unmute"])
        assert result.exit_code != 0

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_connection_error(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 1
        assert "not running" in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_api_key_forwards_to_client(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """--api-key value is forwarded to client.synthesize(api_key=...).

        Per-call key isolation — the user provides a billing-attribution
        key on this single call. Regression guard for vox-a3e: verifies
        the CLI surface that was missing prior to this commit.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "billable work", "--api-key", "sk_project_a"]
        )

        assert result.exit_code == 0
        mock_instance.synthesize.assert_called_once()
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_project_a"

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_api_key_not_echoed_to_output(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """The api key must never appear in stdout, stderr, or logs.

        Security invariant: a secret passed on the command line should
        survive only long enough to reach voxd over the local WebSocket.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        secret = "sk_SECRET_never_echo"
        result = runner.invoke(app, ["unmute", "hello world", "--api-key", secret])

        assert result.exit_code == 0
        assert secret not in result.output
        # JSON mode also must not echo it — the payload only includes id.
        result_json = runner.invoke(
            app,
            ["--json", "unmute", "hello world", "--api-key", secret],
        )
        assert result_json.exit_code == 0
        assert secret not in result_json.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_api_key_empty_argv_normalized_to_none(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty ``--api-key ""`` normalizes to None (anonymous path).

        Symmetric with ``VOX_API_KEY=""``: both mean "no key on this
        path, fall through to file/stdin or anonymous". Replaces the
        previous behavior that raised BadParameter, which shadowed
        mutual-exclusion errors when the env var happened to be set to
        the empty string in CI. Regression guard for Cursor Bugbot on
        PR #175.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key", ""])
        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None

    @patch(f"{_CLI}.VoxClientSync")
    def test_unmute_no_api_key_omits_kwarg(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        """Without --api-key, the client is called with api_key=None.

        A None value lets voxd fall back to the keys.env default.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None


# ---------------------------------------------------------------------------
# API key input path tests
#
# Regression guard for the Cursor Automation security review of PR #175.
# The concern: ``--api-key <value>`` on the command line exposes the
# secret via ``ps``, ``/proc/*/cmdline``, shell history, and terminal
# recordings. Fix keeps ``--api-key`` for back-compat but adds three
# safer input paths (file, stdin, VOX_API_KEY env var) and emits a
# stderr warning when the caller used the argv path directly.
# ---------------------------------------------------------------------------


class TestApiKeyInputPaths:
    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_from_env_var_no_warning(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """VOX_API_KEY populates api_key without firing the argv warning.

        ``/proc/<pid>/environ`` is owner-only by default, so env vars
        are materially harder to snoop than argv. This path must not
        warn.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOX_API_KEY", "sk_env_var_test")
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_env_var_test"
        assert "warning: --api-key on the command line" not in result.stderr

    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_from_argv_emits_warning(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Literal --api-key on the command line emits the stderr warning.

        The key still flows through to the client (back-compat), but
        the user is told loudly that argv is exposed via ps and
        history.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key", "sk_argv_direct"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_argv_direct"
        assert "warning: --api-key on the command line" in result.stderr
        assert "VOX_API_KEY" in result.stderr
        assert "--api-key-file" in result.stderr
        assert "--api-key-stdin" in result.stderr

    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_file_valid(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--api-key-file <path> (mode 0600) delivers the key with no warning.

        Trailing newline is stripped. This is the recommended path
        for stored keys.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file_test\n", encoding="utf-8")
        key_path.chmod(0o600)

        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "hello", "--api-key-file", str(key_path)]
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_file_test"
        assert "warning: --api-key on the command line" not in result.stderr
        assert "accessible to group or other" not in result.stderr

    @pytest.mark.parametrize(
        ("mode", "should_warn", "label"),
        [
            (0o644, True, "world-readable"),
            (0o640, True, "group-readable only"),
            (0o660, True, "group-writable only"),
            (0o600, False, "owner-only (safe)"),
        ],
    )
    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_file_loose_permissions_warn(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: int,
        should_warn: bool,
        label: str,
    ) -> None:
        """Any group or other permission bit fires the advisory warning.

        ``mode & 0o077`` catches 0644 (world-readable), 0640
        (group-readable only), 0660 (group-writable only), and so on.
        Only 0600 is silent. A narrower ``mode & 0o004`` check would
        miss the group-readable case, exposing credentials on shared
        Unix systems where the file's group contains ``nobody``,
        ``www-data``, or a shared-dev account. Regression guard for
        Copilot on PR #175.

        The warning is advisory, not blocking: the call still succeeds
        and the key reaches the client. Matches the keys.env
        permission handling style.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_mode_test\n", encoding="utf-8")
        key_path.chmod(mode)

        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "hello", "--api-key-file", str(key_path)]
        )

        assert result.exit_code == 0, f"{label}: cli exited {result.exit_code}"
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_mode_test"
        if should_warn:
            assert "accessible to group or other" in result.stderr, (
                f"{label} (mode {oct(mode)}): expected permission warning, "
                f"got stderr={result.stderr!r}"
            )
            assert "chmod 600" in result.stderr, (
                f"{label} (mode {oct(mode)}): expected remediation hint, "
                f"got stderr={result.stderr!r}"
            )
        else:
            assert "accessible to group or other" not in result.stderr, (
                f"{label} (mode {oct(mode)}): did not expect permission "
                f"warning, got stderr={result.stderr!r}"
            )

    def test_api_key_file_not_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing path is a BadParameter, not a crash."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        missing = tmp_path / "does_not_exist"

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key-file", str(missing)])

        assert result.exit_code != 0
        assert "is not a file" in result.output or "is not a file" in result.stderr

    def test_api_key_file_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty file is a BadParameter: never silently fall through."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        key_path = tmp_path / "empty.txt"
        key_path.write_text("", encoding="utf-8")
        key_path.chmod(0o600)

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "hello", "--api-key-file", str(key_path)]
        )

        assert result.exit_code != 0
        assert "is empty" in result.output or "is empty" in result.stderr

    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_stdin_pipe(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--api-key-stdin reads one line of piped stdin, strips, no warning.

        Intended usage: ``pass show vox/proj | vox unmute ... --api-key-stdin``.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["unmute", "hello", "--api-key-stdin"],
            input="sk_stdin_test\n",
        )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_stdin_test"
        assert "warning: --api-key on the command line" not in result.stderr

    def test_api_key_stdin_rejects_tty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--api-key-stdin refuses to read when stdin is a tty.

        Interactive prompts would surprise scripts that forgot to
        pipe. The user almost certainly wanted piped input.

        This exercises the helper directly rather than going through
        CliRunner.invoke because click's runner swaps ``sys.stdin``
        for a BytesIO during invocation, so a monkeypatched ``isatty``
        on the real stdin would be masked. The helper is module-level
        and the tty branch is the contract we care about.
        """
        import typer

        from punt_vox.__main__ import (
            _read_api_key_stdin,  # pyright: ignore[reportPrivateUsage]
        )

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        monkeypatch.setattr("punt_vox.__main__.sys.stdin", fake_stdin)

        try:
            _read_api_key_stdin()
        except typer.BadParameter as exc:
            assert "requires piped input" in str(exc)
        else:  # pragma: no cover — forces the failure path
            raise AssertionError("expected typer.BadParameter for tty stdin")

    def test_api_key_stdin_empty_input(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty stdin is a BadParameter, not a silent fall-through."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key-stdin"], input="")

        assert result.exit_code != 0
        assert "empty input" in result.output or "empty input" in result.stderr

    def test_api_key_mutual_exclusion_file_and_stdin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--api-key-file and --api-key-stdin together is a BadParameter.

        Asserts against ``result.exception`` (the raw BadParameter) rather
        than ``result.output``/``result.stderr`` because Click's rich error
        box wraps the rendered message at terminal width. On CI runners
        with narrow ``COLUMNS`` (~80 or less) the flag names and even
        "mutually exclusive" split across lines, breaking substring
        matches. ``standalone_mode=False`` makes Click re-raise
        BadParameter instead of rendering it and calling ``sys.exit`` —
        so ``result.exception`` holds the unwrapped message. Fixes
        intermittent CI failures on PR #175.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file\n", encoding="utf-8")
        key_path.chmod(0o600)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "unmute",
                "hello",
                "--api-key-file",
                str(key_path),
                "--api-key-stdin",
            ],
            input="sk_stdin\n",
            standalone_mode=False,
        )

        assert result.exit_code != 0
        assert isinstance(result.exception, typer.BadParameter), (
            f"expected BadParameter, got "
            f"{type(result.exception).__name__}: {result.exception}"
        )
        exception_msg = str(result.exception)
        assert "mutually exclusive" in exception_msg
        assert "--api-key-file" in exception_msg
        assert "--api-key-stdin" in exception_msg

    def test_api_key_mutual_exclusion_argv_and_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--api-key and --api-key-file together is a BadParameter.

        Asserts against ``result.exception`` for the same reason as
        ``test_api_key_mutual_exclusion_file_and_stdin`` — Click wraps
        the rendered error at terminal width, so substring matches on
        the rendered output are terminal-width sensitive.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file\n", encoding="utf-8")
        key_path.chmod(0o600)

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "unmute",
                "hello",
                "--api-key",
                "sk_argv",
                "--api-key-file",
                str(key_path),
            ],
            standalone_mode=False,
        )

        assert result.exit_code != 0
        assert isinstance(result.exception, typer.BadParameter), (
            f"expected BadParameter, got "
            f"{type(result.exception).__name__}: {result.exception}"
        )
        exception_msg = str(result.exception)
        assert "mutually exclusive" in exception_msg
        assert "--api-key" in exception_msg
        assert "--api-key-file" in exception_msg

    @patch(f"{_CLI}.VoxClientSync")
    def test_api_key_none_source_passes_none(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no api_key source, the client receives api_key=None.

        voxd then falls back to the ambient keys.env value. Regression
        guard that resolve returns None, not an empty string, when
        nothing is configured.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None
        assert "warning: --api-key on the command line" not in result.stderr

    # ------------------------------------------------------------------
    # Empty VOX_API_KEY must not shadow mutual exclusion.
    #
    # Real-world trigger: a CI pipeline exports VOX_API_KEY="" globally
    # (because some jobs use vox and others don't) and then tries to
    # pass --api-key-file or --api-key-stdin on a specific call. Before
    # the fix the empty env var short-circuited to a "cannot be empty"
    # BadParameter before the mutual exclusion check ran. After the
    # fix the empty env string is normalized to None and the file or
    # stdin path proceeds. Regression guard for Cursor Bugbot on PR
    # #175.
    # ------------------------------------------------------------------

    @patch(f"{_CLI}.VoxClientSync")
    def test_empty_vox_api_key_env_does_not_block_file_path(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``VOX_API_KEY=""`` + ``--api-key-file <path>`` uses the file.

        The empty env var must not masquerade as a fourth source, nor
        raise a "cannot be empty" error that hides the intended file
        path from the user.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOX_API_KEY", "")
        key_path = tmp_path / "key.txt"
        key_path.write_text("sk_file_from_empty_env\n", encoding="utf-8")
        key_path.chmod(0o600)

        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app, ["unmute", "hello", "--api-key-file", str(key_path)]
        )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_file_from_empty_env"
        assert "cannot be empty" not in result.output
        assert "mutually exclusive" not in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_empty_vox_api_key_env_does_not_block_stdin_path(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``VOX_API_KEY=""`` + ``--api-key-stdin`` uses the piped value."""
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOX_API_KEY", "")
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["unmute", "hello", "--api-key-stdin"],
            input="sk_stdin_from_empty_env\n",
        )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] == "sk_stdin_from_empty_env"
        assert "cannot be empty" not in result.output
        assert "mutually exclusive" not in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_empty_vox_api_key_env_alone_anonymous(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``VOX_API_KEY=""`` with no other source: anonymous, not an error.

        voxd falls back to the ambient keys.env default.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("VOX_API_KEY", "")
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None

    @patch(f"{_CLI}.VoxClientSync")
    def test_explicit_empty_argv_api_key_also_normalized(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Literal ``--api-key ""`` on argv normalizes to None.

        Locks in symmetric behavior with the env-var path: both the
        empty env value and an explicit empty argv value mean "no
        key", not "user error". Pairs with
        ``test_unmute_api_key_empty_argv_normalized_to_none`` above in
        TestUnmuteCommand, but lives here as well so the full
        empty-source story sits in one place.
        """
        from punt_vox.client import SynthesizeResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VOX_API_KEY", raising=False)
        mock_instance = mock_client_cls.return_value
        mock_instance.synthesize.return_value = SynthesizeResult(request_id="abc")

        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "hello", "--api-key", ""])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_instance.synthesize.call_args[1]
        assert call_kwargs["api_key"] is None


# ---------------------------------------------------------------------------
# record tests
# ---------------------------------------------------------------------------


class TestRecordCommand:
    @patch(f"{_CLI}.VoxClientSync")
    def test_record_basic(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10  # fake MP3

        runner = CliRunner()
        result = runner.invoke(app, ["record", "hello", "-o", str(out)])

        assert result.exit_code == 0
        mock_instance.record.assert_called_once()
        assert out.exists()

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_custom_voice(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Hallo", "--voice", "hans", "-o", str(out)]
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args
        assert call_kwargs[1]["voice"] == "hans"

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_from_file(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(["hello", "world"]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "--from", str(input_file), "-d", str(out_dir)]
        )

        assert result.exit_code == 0
        assert mock_instance.record.call_count == 2

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_voice_settings(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "record",
                "hello",
                "-o",
                str(out),
                "--stability",
                "0.5",
                "--similarity",
                "0.7",
                "--style",
                "0.3",
                "--speaker-boost",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["stability"] == 0.5
        assert call_kwargs["similarity"] == 0.7
        assert call_kwargs["style"] == 0.3
        assert call_kwargs["speaker_boost"] is True

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_with_language(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
        monkeypatch: MagicMock,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app, ["record", "Guten Tag", "--language", "de", "-o", str(out)]
        )

        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["language"] == "de"

    @patch(f"{_CLI}.VoxClientSync")
    def test_record_connection_error(
        self,
        mock_client_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from punt_vox.client import VoxdConnectionError

        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["record", "hello", "-o", str(out)])

        assert result.exit_code == 1
        assert "not running" in result.output


# ---------------------------------------------------------------------------
# vibe tests
# ---------------------------------------------------------------------------


class TestVibeCommand:
    def test_vibe_mood(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "excited"])
        assert result.exit_code == 0
        assert "excited" in result.output

    def test_vibe_auto(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "auto"])
        assert result.exit_code == 0
        assert "auto" in result.output

    def test_vibe_off(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        runner = CliRunner()
        result = runner.invoke(app, ["vibe", "off"])
        assert result.exit_code == 0
        assert "off" in result.output


# ---------------------------------------------------------------------------
# notify/speak/voice tests
# ---------------------------------------------------------------------------


class TestNotifyCommand:
    def test_notify_y(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "y"])
        assert result.exit_code == 0
        assert "enabled" in result.output.lower()

    def test_notify_n(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "n"])
        assert result.exit_code == 0
        assert "disabled" in result.output.lower()

    def test_notify_c(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c"])
        assert result.exit_code == 0
        assert "continuous" in result.output.lower()

    def test_notify_c_always_enables_speak(
        self, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        """Continuous mode always sets speak=y, even if file exists."""
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        config.write_text('---\nspeak: "n"\nnotify: "n"\n---\n')
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c"])
        assert result.exit_code == 0
        text = config.read_text()
        assert 'speak: "y"' in text
        assert 'notify: "c"' in text

    def test_notify_c_with_voice(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "c", "--voice", "matilda"])
        assert result.exit_code == 0
        text = config.read_text()
        assert 'voice: "matilda"' in text
        assert 'notify: "c"' in text
        assert 'speak: "y"' in text

    def test_notify_invalid(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        config = tmp_path / "config.md"
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["notify", "x"])
        assert result.exit_code == 1


class TestSpeakCommand:
    def test_speak_y(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["speak", "y"])
        assert result.exit_code == 0
        assert "voice on" in result.output.lower()

    def test_speak_n(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["speak", "n"])
        assert result.exit_code == 0
        assert "chimes" in result.output.lower()


class TestVoiceCommand:
    def test_voice(self, tmp_path: Path, monkeypatch: MagicMock) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)
        monkeypatch.setattr("punt_vox.__main__.find_config", lambda: config)

        runner = CliRunner()
        result = runner.invoke(app, ["voice", "matilda"])
        assert result.exit_code == 0
        assert "matilda" in result.output.lower()


# ---------------------------------------------------------------------------
# version tests
# ---------------------------------------------------------------------------


class TestVersionCommand:
    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "vox" in result.output


# ---------------------------------------------------------------------------
# status tests
# ---------------------------------------------------------------------------


class TestStatusCommand:
    @patch(f"{_CLI}.VoxClientSync")
    def test_status_daemon_running(
        self, mock_client_cls: MagicMock, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        import punt_vox.config as cfg

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        mock_instance = mock_client_cls.return_value
        mock_instance.health.return_value = {"provider": "elevenlabs"}

        runner = CliRunner()
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "Daemon" in result.output
        assert "running" in result.output

    @patch(f"{_CLI}.VoxClientSync")
    def test_status_daemon_not_running(
        self, mock_client_cls: MagicMock, tmp_path: Path, monkeypatch: MagicMock
    ) -> None:
        import punt_vox.config as cfg
        from punt_vox.client import VoxdConnectionError

        config = tmp_path / "config.md"
        monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", config)

        mock_instance = mock_client_cls.return_value
        mock_instance.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "not running" in result.output


# ---------------------------------------------------------------------------
# main group tests
# ---------------------------------------------------------------------------


class TestMainGroup:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "vox" in result.output.lower()

    def test_unmute_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["unmute", "--help"])
        assert result.exit_code == 0
        assert "voice" in result.output.lower()

    def test_verbose_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "--help"])
        assert result.exit_code == 0

    @patch(f"{_CLI}.VoxClientSync")
    def test_provider_flag(self, mock_client_cls: MagicMock, tmp_path: Path) -> None:
        out = tmp_path / "test.mp3"
        mock_instance = mock_client_cls.return_value
        mock_instance.record.return_value = b"\xff\xfb\x90\x00" * 10

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["record", "hello", "--provider", "polly", "-o", str(out)],
        )
        assert result.exit_code == 0
        call_kwargs = mock_instance.record.call_args[1]
        assert call_kwargs["provider"] == "polly"


# ---------------------------------------------------------------------------
# doctor tests
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    def _run_doctor(
        self,
        tmp_path: Path,
        *,
        ffmpeg_found: bool = True,
        uvx_found: bool = True,
        config_exists: bool = False,
        config_data: dict[str, object] | None = None,
        system_platform: str = "Darwin",
        espeak_found: str | None = None,
        daemon_healthy: bool = True,
        daemon_version: str | None = "4.2.0",
        installed_version: str = "4.2.0",
    ) -> Result:
        """Invoke doctor with controlled mocks.

        ``daemon_version`` and ``installed_version`` default to the same
        value so the mismatch warning does not fire unless the test
        explicitly diverges them. Pass ``daemon_version=None`` to
        simulate a pre-upgrade daemon that predates the health-version
        field.
        """

        def which_side_effect(name: str) -> str | None:
            if name == "ffmpeg" and ffmpeg_found:
                return "/opt/homebrew/bin/ffmpeg"
            if name == "uvx" and uvx_found:
                return "/usr/local/bin/uvx"
            if name in ("espeak-ng", "espeak") and espeak_found == name:
                return f"/usr/bin/{name}"
            return None

        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        if config_exists:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(config_data or {}))

        mock_client = MagicMock()
        if daemon_healthy:
            health_payload: dict[str, object] = {
                "provider": "elevenlabs",
                "active_sessions": 2,
                "port": 8421,
            }
            if daemon_version is not None:
                health_payload["daemon_version"] = daemon_version
            mock_client.health.return_value = health_payload
        else:
            from punt_vox.client import VoxdConnectionError

            mock_client.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(
                f"{_CLI}.installed_version",
                return_value=installed_version,
            ),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
            patch(
                f"{_CLI}.default_output_dir",
                return_value=tmp_path / "audio",
            ),
            patch(f"{_CLI}.platform.system", return_value=system_platform),
        ):
            result = runner.invoke(app, ["doctor"])

        return result

    def test_all_required_pass(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path)
        assert result.exit_code == 0
        assert "\u2713 Python" in result.output
        assert "\u2713 ffmpeg" in result.output
        assert "Daemon: running" in result.output

    def test_ffmpeg_missing_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, ffmpeg_found=False)
        assert result.exit_code == 1
        assert "\u2717 ffmpeg" in result.output

    def test_uvx_missing_is_optional(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, uvx_found=False)
        assert result.exit_code == 0
        assert "\u25cb uvx" in result.output

    def test_daemon_not_running_fails(self, tmp_path: Path) -> None:
        result = self._run_doctor(tmp_path, daemon_healthy=False)
        assert result.exit_code == 1
        assert "Daemon: not running" in result.output

    def test_linux_no_keys_no_espeak_warns(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            result = self._run_doctor(
                tmp_path, system_platform="Linux", espeak_found=None
            )
        assert result.exit_code == 0
        assert "espeak-ng/espeak: not found" in result.output

    def test_matching_versions_passes_without_warning(self, tmp_path: Path) -> None:
        """Daemon version == installed wheel version: green checkmark, no warn."""
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.2.0",
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u2713 Daemon: running" in result.output
        assert "version 4.2.0" in result.output
        assert "\u26a0" not in result.output

    def test_mismatched_versions_warns_without_failing(self, tmp_path: Path) -> None:
        """Running daemon older than installed wheel: warning, exit code 0.

        This is the vox-nmb regression guard. A stale voxd survived
        v4.2.0 release-day smoke tests because doctor only checked
        reachability, not version alignment. Doctor now warns but does
        not fail — the daemon is still functional, just out of date.
        """
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.1.1",
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u26a0 Daemon: running" in result.output
        assert "version 4.1.1" in result.output
        assert "wheel has 4.2.0" in result.output
        assert "'vox daemon restart'" in result.output
        # The refresh hint must NOT be prefixed with ``sudo``: ``vox
        # daemon restart`` refuses to run as root and invokes sudo
        # internally only for the service-manager calls that need it.
        # A literal copy-paste of the hint must work unprivileged.
        # Regression guard for Copilot round-2 finding on PR #175.
        # Built dynamically so the source of this test file does not
        # itself contain the forbidden phrase.
        forbidden_hint = " ".join(["sudo", "vox", "daemon", "restart"])
        assert forbidden_hint not in result.output
        # Summary line should flag the warning count.
        assert "1 warning" in result.output

    def test_mismatched_versions_json_mode(self, tmp_path: Path) -> None:
        """--json output includes the warned count for machine consumption."""
        result = self._run_doctor(
            tmp_path,
            daemon_version="4.1.1",
            installed_version="4.2.0",
        )
        # The helper invokes without --json; re-run with explicit --json flag.
        runner = CliRunner()

        def which_side_effect(name: str) -> str | None:
            if name == "ffmpeg":
                return "/opt/homebrew/bin/ffmpeg"
            if name == "uvx":
                return "/usr/local/bin/uvx"
            return None

        mock_client = MagicMock()
        mock_client.health.return_value = {
            "provider": "elevenlabs",
            "active_sessions": 2,
            "port": 8421,
            "daemon_version": "4.1.1",
        }

        with (
            patch(f"{_CLI}.shutil.which", side_effect=which_side_effect),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="4.2.0"),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=tmp_path / "nope.json",
            ),
            patch(
                f"{_CLI}.default_output_dir",
                return_value=tmp_path / "audio",
            ),
            patch(f"{_CLI}.platform.system", return_value="Darwin"),
        ):
            result = runner.invoke(app, ["--json", "doctor"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["warned"] >= 1

    def test_pre_version_daemon_falls_back_to_pass(self, tmp_path: Path) -> None:
        """An older daemon that lacks daemon_version still reports PASS.

        Daemons built before commit 2 do not include daemon_version in
        their health payload. Doctor must not treat that as a mismatch
        — it cannot tell the version, so it cannot warn intelligently.
        Fall back to the existing "Daemon: running" pass.
        """
        result = self._run_doctor(
            tmp_path,
            daemon_version=None,
            installed_version="4.2.0",
        )
        assert result.exit_code == 0
        assert "\u2713 Daemon: running" in result.output
        assert "\u26a0" not in result.output


# ---------------------------------------------------------------------------
# install tests (marketplace)
# ---------------------------------------------------------------------------


class TestInstallCommand:
    def test_install_success(self) -> None:
        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value="/usr/bin/claude"),
            patch(f"{_CLI}.subprocess.run") as mock_run,
            patch("punt_vox.service.install", return_value="voxd running"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["install"])

        assert result.exit_code == 0
        assert "Restart Claude Code" in result.output

    def test_install_no_claude(self) -> None:
        runner = CliRunner()
        with patch(f"{_CLI}.shutil.which", return_value=None):
            result = runner.invoke(app, ["install"])

        assert result.exit_code != 0


class TestUninstallCommand:
    def test_uninstall_success(self) -> None:
        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value="/usr/bin/claude"),
            patch(f"{_CLI}.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, ["uninstall"])

        assert result.exit_code == 0
        assert "Uninstalled." in result.output


# ---------------------------------------------------------------------------
# install-desktop tests (Claude Desktop MCP registration)
# ---------------------------------------------------------------------------

_UVX = "/usr/local/bin/uvx"


class TestInstallDesktopCommand:
    def test_creates_config_from_scratch(self, tmp_path: Path) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        audio_dir = tmp_path / "audio"

        runner = CliRunner()
        with (
            patch(
                f"{_CLI}.shutil.which",
                side_effect=lambda name: (  # pyright: ignore[reportUnknownLambdaType]
                    _UVX if name == "uvx" else "/usr/bin/say" if name == "say" else None
                ),
            ),
            patch(
                f"{_CLI}._claude_desktop_config_path",
                return_value=config_path,
            ),
            patch("punt_vox.providers.platform.system", return_value="Darwin"),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            result = runner.invoke(
                app,
                ["install-desktop", "--output-dir", str(audio_dir)],
            )

        assert result.exit_code == 0
        assert config_path.exists()

        data = json.loads(config_path.read_text())
        server = data["mcpServers"]["tts"]
        assert server["command"] == _UVX
        assert server["args"] == ["--from", "punt-vox", "vox", "mcp"]
        assert server["env"]["VOX_OUTPUT_DIR"] == str(audio_dir)
        assert server["env"]["TTS_PROVIDER"] == "say"

    def test_preserves_other_servers(self, tmp_path: Path) -> None:
        config_path = tmp_path / "Claude" / "claude_desktop_config.json"
        config_path.parent.mkdir(parents=True)
        existing: dict[str, object] = {
            "mcpServers": {
                "other-server": {"command": "other", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing))

        runner = CliRunner()
        with (
            patch(f"{_CLI}.shutil.which", return_value=_UVX),
            patch(f"{_CLI}._claude_desktop_config_path", return_value=config_path),
        ):
            result = runner.invoke(
                app,
                ["install-desktop", "--output-dir", str(tmp_path / "audio")],
            )

        assert result.exit_code == 0
        data = json.loads(config_path.read_text())
        assert "other-server" in data["mcpServers"]
        assert "tts" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Global flag tests
# ---------------------------------------------------------------------------


class TestGlobalFlags:
    def test_short_help_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-h"])
        assert result.exit_code == 0
        assert "Text-to-speech CLI." in result.output

    def test_quiet_suppresses_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-q", "version"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    @patch(f"{_CLI}.VoxClientSync")
    def test_quiet_suppresses_status(self, mock_client_cls: MagicMock) -> None:
        from punt_vox.client import VoxdConnectionError

        mock_instance = mock_client_cls.return_value
        mock_instance.health.side_effect = VoxdConnectionError("not running")

        runner = CliRunner()
        result = runner.invoke(app, ["-q", "status"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    @patch(f"{_CLI}.VoxClientSync")
    def test_json_still_emits_with_quiet(self, mock_client_cls: MagicMock) -> None:
        mock_instance = mock_client_cls.return_value
        mock_instance.health.return_value = {"provider": "polly"}

        runner = CliRunner()
        result = runner.invoke(app, ["--json", "-q", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "provider" in data

    def test_verbose_quiet_mutual_exclusion(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["-v", "-q", "version"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# daemon restart tests
# ---------------------------------------------------------------------------


class TestDaemonRestartCommand:
    """``vox daemon restart`` cycles voxd via the service manager.

    Regression guard for vox-nmb: a stale voxd survived v4.2.0's
    release-day verification because doctor reported "Daemon: running"
    without checking whether the running process matched the on-disk
    wheel. The restart command is the second half of the fix (the first
    half is the version-mismatch warning in commit 3).
    """

    def test_refuses_to_run_as_root(self) -> None:
        """Refuse running as root — sudo is invoked internally only."""
        runner = CliRunner()
        with patch(f"{_CLI}.os.geteuid", return_value=0):
            result = runner.invoke(app, ["daemon", "restart"])
        assert result.exit_code != 0
        assert "without sudo" in result.output or "not root" in result.output

    def test_daemon_restart_refuses_windows(self) -> None:
        """Windows gets a clear platform error, not an AttributeError crash.

        ``os.geteuid`` is POSIX-only; on Windows it raises
        ``AttributeError``. The platform guard must fire BEFORE
        ``os.geteuid`` is called so the user sees a typed BadParameter
        explaining that vox daemon restart only supports macOS and
        Linux. Regression guard for Cursor Bugbot on PR #175.

        Uses ``standalone_mode=False`` so the raised BadParameter is
        exposed on ``result.exception`` (same pattern as the mutual
        exclusion tests above).
        """
        runner = CliRunner()
        with patch(f"{_CLI}.sys.platform", "win32"):
            result = runner.invoke(app, ["daemon", "restart"], standalone_mode=False)
        assert result.exit_code != 0
        assert isinstance(result.exception, typer.BadParameter), (
            f"expected BadParameter, got "
            f"{type(result.exception).__name__}: {result.exception}"
        )
        assert "Windows" in str(result.exception)

    def test_unsupported_platform_fails(self) -> None:
        """Windows (or anything else) raises SystemExit from detect_platform."""
        runner = CliRunner()
        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch(
                "punt_vox.service.detect_platform",
                side_effect=SystemExit("Unsupported platform: Windows."),
            ),
        ):
            result = runner.invoke(app, ["daemon", "restart"])
        assert result.exit_code != 0

    def test_linux_subprocess_argv_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux path invokes ``sudo systemctl start voxd`` exactly once.

        This test intentionally stubs out ``_systemd_stop`` and
        ``_ensure_port_free`` via ``monkeypatch.setattr`` on the real
        module object — not ``patch(...)`` with a string path — so a
        rename of either private helper fails at import time instead of
        silently neutering the test. The assertion target is the
        subprocess argv shape for the service-manager start step, which
        is the only CLI-boundary behavior this test isolates. The full
        happy-path sequence (including the real ``_systemd_stop`` and
        ``_ensure_port_free``) is exercised by
        ``test_happy_path_drives_full_sequence_through_health_probe``.
        """
        from punt_vox import service

        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.return_value = {
            "pid": 42,
            "port": 8421,
            "daemon_version": "9.9.9-test",
        }

        calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            calls.append(tuple(argv))
            return MagicMock(returncode=0)

        # Rename-safe stubs: AttributeError at import/setup, not at call.
        monkeypatch.setattr(service, "_systemd_stop", lambda: None)
        monkeypatch.setattr(service, "_ensure_port_free", lambda: None)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="9.9.9-test"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        # The only subprocess invocation should be the systemctl start
        # step — that is the CLI-boundary contract this test pins down.
        assert calls == [("sudo", "systemctl", "start", "voxd")]
        # User-visible confirmation also includes the version, so a
        # regression that drops the version from the success line is
        # caught here too.
        assert "pid=42" in result.output
        assert "port 8421" in result.output
        assert "9.9.9-test" in result.output

    def test_macos_subprocess_argv_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """macOS path invokes launchctl load + kickstart in order.

        Same rename-safe stubbing strategy as the linux variant — see
        ``test_linux_subprocess_argv_shape`` for the rationale.
        """
        from punt_vox import service

        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.return_value = {
            "pid": 99,
            "port": 8421,
            "daemon_version": "9.9.9-test",
        }

        calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            calls.append(tuple(argv))
            return MagicMock(returncode=0)

        monkeypatch.setattr(service, "_launchd_stop", lambda: None)
        monkeypatch.setattr(service, "_ensure_port_free", lambda: None)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=501),
            patch("punt_vox.service.detect_platform", return_value="macos"),
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="9.9.9-test"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        assert len(calls) == 2
        # First call: launchctl load -w
        assert calls[0] == (
            "sudo",
            "launchctl",
            "load",
            "-w",
            "/Library/LaunchDaemons/com.punt-labs.voxd.plist",
        )
        # Second call: launchctl kickstart -k
        assert calls[1] == (
            "sudo",
            "launchctl",
            "kickstart",
            "-k",
            "system/com.punt-labs.voxd",
        )
        assert "pid=99" in result.output
        assert "9.9.9-test" in result.output

    def test_happy_path_drives_full_sequence_through_health_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: run the real stop + ensure-port-free helpers.

        The other restart tests stub ``_systemd_stop`` and
        ``_ensure_port_free`` to isolate the CLI-boundary behavior.
        This test does NOT — it patches only the low-level primitives
        each helper calls (``subprocess.run`` for systemctl, unit-file
        existence, and ``_find_pid_on_port`` for the port re-check).
        That way a regression inside either helper, or a control-flow
        error that skips calling one of them, is caught here instead
        of having to read the assertion annotations to decide whether
        ``assert_called_once`` covers the thing the PR changed.

        The linux path is the exhaustive sequence because macOS
        ``launchctl`` has its own argv-shape coverage above.
        """
        from punt_vox import service

        runner = CliRunner()

        # Happy health response: matches the (patched) wheel version.
        mock_client = MagicMock()
        mock_client.health.return_value = {
            "pid": 1234,
            "port": 8421,
            "daemon_version": "9.9.9-test",
        }

        subprocess_calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            subprocess_calls.append(tuple(argv))
            return MagicMock(returncode=0)

        # Pretend the systemd unit file exists so ``_systemd_stop``
        # actually drives ``subprocess.run`` for the stop step instead
        # of short-circuiting on a missing unit file.
        fake_unit = MagicMock()
        fake_unit.exists.return_value = True
        monkeypatch.setattr("punt_vox.service._SYSTEMD_UNIT", fake_unit)

        def no_port_file() -> None:
            return None

        def no_stale() -> bool:
            return False

        def no_pids(_port: int) -> list[int]:
            return []

        # No daemon on the host port, nothing to kill — exercises the
        # "empty kill path" branch of ``_ensure_port_free`` without
        # the test depending on whatever happens to bind 8421 on CI.
        monkeypatch.setattr("punt_vox.service.read_port_file", no_port_file)
        monkeypatch.setattr("punt_vox.service._kill_stale_daemon", no_stale)
        monkeypatch.setattr("punt_vox.service._find_pid_on_port", no_pids)
        # subprocess.run is called from BOTH the service module (for
        # ``_systemd_stop``) and __main__ (for the start step). Both
        # funnel through ``subprocess.run`` — patch both to capture
        # the complete argv history.
        monkeypatch.setattr("punt_vox.service.subprocess.run", fake_run)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="9.9.9-test"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        # Rename-safety check: referencing the real module attribute.
        # If ``_ensure_port_free`` or ``_systemd_stop`` is renamed in
        # service.py, the ``from punt_vox import service`` attribute
        # access below raises AttributeError at test setup — well
        # before the subprocess expectations even get checked.
        assert callable(service._systemd_stop)  # pyright: ignore[reportPrivateUsage]
        assert callable(service._ensure_port_free)  # pyright: ignore[reportPrivateUsage]
        # Full subprocess sequence: stop voxd (from _systemd_stop),
        # then start voxd (from daemon_restart_cmd). Anything else
        # means the sequence changed and the user-visible contract
        # with it.
        assert subprocess_calls == [
            ("sudo", "systemctl", "stop", "voxd"),
            ("sudo", "systemctl", "start", "voxd"),
        ], f"unexpected subprocess sequence: {subprocess_calls}"
        assert "pid=1234" in result.output
        assert "9.9.9-test" in result.output

    def test_health_retry_before_success(self) -> None:
        """Daemon takes two poll cycles to come back — restart still succeeds."""
        from punt_vox.client import VoxdConnectionError

        runner = CliRunner()

        mock_client = MagicMock()
        # First two polls fail, third succeeds.
        mock_client.health.side_effect = [
            VoxdConnectionError("not yet"),
            VoxdConnectionError("not yet"),
            {"pid": 7, "port": 8421, "daemon_version": "9.9.9-test"},
        ]

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.time.sleep") as mock_sleep,
            patch(f"{_CLI}.installed_version", return_value="9.9.9-test"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 0, result.output
        assert mock_client.health.call_count == 3
        assert mock_sleep.call_count == 2
        assert "pid=7" in result.output

    def test_restart_fails_on_version_mismatch(self) -> None:
        """Running daemon version differs from wheel: restart must fail closed.

        vox-nmb: a silent stop failure can leave the OLD daemon alive,
        in which case ``systemctl start voxd`` exits 0 as a no-op on an
        already-active unit. Without this check, the command would
        print success while the stale daemon continues to answer — the
        exact bug the feature exists to prevent.
        """
        runner = CliRunner()

        mock_client = MagicMock()
        # Running daemon is v4.1.1 but wheel is v4.2.0 — simulated
        # stale process that the service manager failed to restart.
        mock_client.health.return_value = {
            "pid": 42,
            "port": 8421,
            "daemon_version": "4.1.1",
        }

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="4.2.0"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        # Error message must call out BOTH versions so the operator can
        # tell what's stale without cracking open logs.
        assert "4.1.1" in result.output
        assert "4.2.0" in result.output
        assert "voxd.log" in result.output

    def test_restart_reports_port_contention(self) -> None:
        """``_ensure_port_free`` raising ``SystemExit`` must reach the user.

        Typer's runner swallows raw SystemExit without printing the
        message argument. Without the explicit try/except translation,
        the user would see a silent exit-1 with no indication that
        port contention was the cause — a poor experience for the
        load-bearing new command.
        """
        runner = CliRunner()

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch(
                "punt_vox.service._ensure_port_free",
                side_effect=SystemExit("Port 8421 is still in use (PIDs: [5555])."),
            ),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "Error" in result.output
        assert "port still occupied" in result.output.lower() or (
            "still in use" in result.output
        )
        assert "8421" in result.output
        assert "voxd.log" in result.output

    def test_restart_fails_on_absent_version(self) -> None:
        """Health response missing ``daemon_version``: restart must fail closed.

        A daemon built from pre-cef3e8a code cannot self-report its
        version — the health route returns no ``daemon_version`` field.
        We cannot prove the running process matches the wheel, and the
        stale-daemon symptom we're trying to detect looks exactly like
        this. Fail closed instead of printing success on ambiguous data.
        """
        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.return_value = {"pid": 42, "port": 8421}

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.installed_version", return_value="4.2.0"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "did not report a version" in result.output
        assert "4.2.0" in result.output

    def test_start_subprocess_failure_exits_with_log_hint(self) -> None:
        """systemctl start failure exits 1 and points at the voxd log."""
        runner = CliRunner()

        def fake_run(
            argv: list[str],
            *,
            check: bool = False,
        ) -> MagicMock:
            raise subprocess.CalledProcessError(1, argv)

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "voxd.log" in result.output

    def test_daemon_never_comes_back_exits_with_log_hint(self) -> None:
        """Health never succeeds within the 5s window — exit 1 with log hint."""
        from punt_vox.client import VoxdConnectionError

        runner = CliRunner()

        mock_client = MagicMock()
        mock_client.health.side_effect = VoxdConnectionError("refused")

        # Fake time.monotonic to immediately expire the deadline.
        ticks = iter([0.0, 0.0, 100.0])

        with (
            patch(f"{_CLI}.os.geteuid", return_value=1000),
            patch("punt_vox.service.detect_platform", return_value="linux"),
            patch("punt_vox.service._systemd_stop"),
            patch("punt_vox.service._ensure_port_free"),
            patch(f"{_CLI}.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(f"{_CLI}.VoxClientSync", return_value=mock_client),
            patch(f"{_CLI}.time.monotonic", side_effect=lambda: next(ticks)),
            patch(f"{_CLI}.time.sleep"),
        ):
            result = runner.invoke(app, ["daemon", "restart"])

        assert result.exit_code == 1
        assert "voxd.log" in result.output
        assert "refused" in result.output
