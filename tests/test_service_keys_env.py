"""Tests for punt_vox.service.keys_env — provider key file writing."""

from __future__ import annotations

import logging
import os
import stat as stat_mod
from pathlib import Path

import pytest

from punt_vox.service.keys_env import KeysEnvWriter


@pytest.fixture()
def writer() -> KeysEnvWriter:
    return KeysEnvWriter()


def _open_fd_count() -> int | None:
    """Return this process's open-fd count, or None when it can't be read.

    Prefer ``/proc/self/fd`` (canonical on Linux, present whenever /proc is
    mounted); fall back to ``/dev/fd`` (macOS/BSD). The fd count is best-effort
    defence-in-depth -- the caller's core secret-cleanup assertions are
    unconditional -- so any OS-level failure to enumerate the directory
    (missing, a non-directory, permission-denied, ...) returns None to skip only
    the fd check, never to error the test over an environment quirk. ``OSError``
    is the base of FileNotFoundError, NotADirectoryError, and PermissionError,
    so catching it covers every such enumeration failure in one clause.
    """
    for fd_dir in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return sum(1 for _ in fd_dir.iterdir())
        except OSError:
            continue
    return None


def test_write_keys_env_creates_file_at_target_path(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """write() writes keys.env at the exact path the caller passed."""
    keys_path = tmp_path / "state" / "keys.env"
    env = {
        "ELEVENLABS_API_KEY": "sk-eleven-test",
        "TTS_PROVIDER": "elevenlabs",
    }
    result = writer.write(env, keys_path)
    assert result == keys_path
    assert keys_path.exists()
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY=sk-eleven-test" in content
    assert "TTS_PROVIDER=elevenlabs" in content


def test_write_keys_env_mode_0600(writer: KeysEnvWriter, tmp_path: Path) -> None:
    """keys.env must always be chmod 0600 — it holds provider secrets."""
    keys_path = tmp_path / "keys.env"
    writer.write({"OPENAI_API_KEY": "sk-test"}, keys_path)
    mode = stat_mod.S_IMODE(keys_path.stat().st_mode)
    assert mode == 0o600, f"keys.env mode is {oct(mode)}, expected 0o600"


def test_write_keys_env_is_atomic_and_leaves_no_temp(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """The atomic temp+replace write leaves only keys.env, no temp files behind."""
    keys_path = tmp_path / "keys.env"
    writer.write({"OPENAI_API_KEY": "sk-clean"}, keys_path)
    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert survivors == ["keys.env"]  # the temp was renamed into place, not orphaned
    assert "OPENAI_API_KEY=sk-clean" in keys_path.read_text(encoding="utf-8")


def test_write_keys_env_mid_write_failure_leaks_no_fd_or_temp(
    writer: KeysEnvWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-write fsync failure cleans up the secret-bearing temp and leaks no fd.

    Inject the failure at ``os.fsync`` -- after the secret bytes are written and
    flushed into the temp file but before the atomic rename -- so the test
    actually reaches the state a naive writer would leave a plaintext-secret temp
    behind. ``_harden_parent`` runs first and must succeed, so control genuinely
    reaches ``AtomicFile.replace``; patching the parent ``chmod`` instead would
    abort before any temp ever holds the secret and prove nothing.
    """
    keys_path = tmp_path / "keys.env"

    def _boom(_fd: int) -> None:
        msg = "fsync refused"
        raise OSError(msg)

    fds_before = _open_fd_count()
    monkeypatch.setattr(os, "fsync", _boom)
    with pytest.raises(OSError, match="fsync refused"):
        writer.write({"OPENAI_API_KEY": "sk-secret"}, keys_path)

    # (b) no partial credentials file, (c) no orphaned secret-bearing temp.
    assert not keys_path.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []
    # (d) the temp's fd was closed on the failure path, not leaked. Only the
    # fd-count check is environment-dependent; skip it (never error) where no
    # kernel fd directory is exposed, leaving the guarantees above unconditional.
    if fds_before is None:
        pytest.skip("no /proc/self/fd or /dev/fd available for the fd-leak check")
    assert _open_fd_count() == fds_before


def test_write_keys_env_forces_0600_over_existing_wider_file(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """An existing world-readable keys.env is tightened to 0600 on rewrite.

    AtomicFile preserves an existing file's mode by default; keys_env forces
    0600 (``replace(mode=0o600)``) so a secrets file that was somehow left at a
    wider mode is narrowed rather than preserved -- the security invariant that a
    plain mode-preserving write would silently violate.
    """
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("OPENAI_API_KEY=old\n", encoding="utf-8")
    keys_path.chmod(0o644)
    writer.write({"OPENAI_API_KEY": "sk-new"}, keys_path)
    assert stat_mod.S_IMODE(keys_path.stat().st_mode) == 0o600
    assert "OPENAI_API_KEY=sk-new" in keys_path.read_text(encoding="utf-8")


def test_write_keys_env_cleanup_never_masks_the_write_error(
    writer: KeysEnvWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising unlink is suppressed so the caller sees the real write error."""
    keys_path = tmp_path / "keys.env"

    def _write_boom(*_args: object, **_kwargs: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    def _unlink_boom(*_args: object, **_kwargs: object) -> None:
        msg = "unlink refused"
        raise OSError(msg)

    monkeypatch.setattr(os, "fsync", _write_boom)  # fail mid-write
    monkeypatch.setattr(Path, "unlink", _unlink_boom)  # cleanup also fails
    # The caller sees the real cause ("disk full"), not the cleanup's "unlink refused".
    with pytest.raises(OSError, match="disk full"):
        writer.write({"OPENAI_API_KEY": "sk-x"}, keys_path)


def test_write_keys_env_fdopen_failure_leaves_no_temp(
    writer: KeysEnvWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising fdopen closes the raw fd and orphans no temp file."""
    keys_path = tmp_path / "keys.env"

    def _fdopen_boom(*_args: object, **_kwargs: object) -> None:
        msg = "fdopen refused"
        raise OSError(msg)

    monkeypatch.setattr(os, "fdopen", _fdopen_boom)
    with pytest.raises(OSError, match="fdopen refused"):
        writer.write({"OPENAI_API_KEY": "sk-x"}, keys_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_write_keys_env_preserves_existing_keys(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """Keys already in the file are preserved when not overridden."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text(
        "# header\nELEVENLABS_API_KEY=original-eleven\nOPENAI_API_KEY=original-openai\n"
    )
    writer.write({"OPENAI_API_KEY": "new-openai"}, keys_path)
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY=original-eleven" in content
    assert "OPENAI_API_KEY=new-openai" in content


def test_write_keys_env_keeps_existing_key_when_env_value_empty(
    writer: KeysEnvWriter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty env value never deletes a stored credential, and says so.

    A failed secret-manager lookup exports ``KEY=""`` rather than leaving it
    unset; treating that as "remove" silently wiped working keys on install.
    """
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("ELEVENLABS_API_KEY=sk-working-eleven\n")
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({"ELEVENLABS_API_KEY": ""}, keys_path)
    assert "ELEVENLABS_API_KEY=sk-working-eleven" in keys_path.read_text()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ELEVENLABS_API_KEY" in r.getMessage() for r in warnings)
    assert all("sk-working-eleven" not in r.getMessage() for r in caplog.records)


def test_write_keys_env_empty_value_for_absent_key_writes_nothing_quietly(
    writer: KeysEnvWriter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty value for a key the file lacks writes no line and logs nothing."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({"ELEVENLABS_API_KEY": "", "OPENAI_API_KEY": "sk-o"}, keys_path)
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY" not in content
    assert "OPENAI_API_KEY=sk-o" in content
    assert caplog.records == []


def test_write_keys_env_override_and_absent_keys_log_nothing(
    writer: KeysEnvWriter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-empty override and a key absent from env are silent, as before."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("ELEVENLABS_API_KEY=sk-kept\nOPENAI_API_KEY=sk-old\n")
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({"OPENAI_API_KEY": "sk-new"}, keys_path)
    content = keys_path.read_text()
    assert "ELEVENLABS_API_KEY=sk-kept" in content
    assert "OPENAI_API_KEY=sk-new" in content
    assert caplog.records == []


def test_write_keys_env_non_header_setting_keeps_spaces(
    writer: KeysEnvWriter, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A plain setting such as AWS_PROFILE never goes in a header; a space is fine."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({"AWS_PROFILE": "my profile"}, keys_path)
    assert "AWS_PROFILE=my profile" in keys_path.read_text()
    assert caplog.records == []


@pytest.mark.parametrize(
    "key", ["AWS_PROFILE", "TTS_PROVIDER", "AWS_SECRET_ACCESS_KEY"]
)
def test_write_keys_env_non_header_setting_refuses_control_chars(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    key: str,
) -> None:
    """Non-header values still get the line-injection refusal, by key name."""
    keys_path = tmp_path / "keys.env"
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({key: "x\nOPENAI_API_KEY=injected"}, keys_path)
    assert "injected" not in keys_path.read_text()
    messages = [r.getMessage() for r in caplog.records]
    assert any(key in m and "control characters" in m for m in messages)
    assert all("injected" not in m for m in messages)


@pytest.mark.parametrize(
    "key",
    ["ELEVENLABS_API_KEY", "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN"],
)
def test_write_keys_env_header_carried_keys_refuse_spaces(
    writer: KeysEnvWriter, tmp_path: Path, key: str
) -> None:
    """Every value sent as HTTP header content gets the header-safe check."""
    keys_path = tmp_path / "keys.env"
    writer.write({key: "abc def"}, keys_path)
    assert key not in keys_path.read_text()


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param("sk-abc def", id="inner-space"),
        pytest.param("sk-abc ", id="trailing-space"),
        pytest.param("\tsk-abc", id="leading-tab"),
        pytest.param("sk-abc\r", id="carriage-return"),
        pytest.param("\u201csk-abc\u201d", id="smart-quotes"),
        pytest.param("sk-\u00e9abc", id="non-ascii-letter"),
        pytest.param("sk-abc\x7f", id="delete-char"),
    ],
)
def test_write_keys_env_refuses_value_no_header_can_carry(
    writer: KeysEnvWriter,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    bad_value: str,
) -> None:
    """A value outside printable non-space ASCII is refused, key named, value not."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("ELEVENLABS_API_KEY=sk-previous\n")
    with caplog.at_level(logging.WARNING, logger="punt_vox.service.keys_env"):
        writer.write({"ELEVENLABS_API_KEY": bad_value}, keys_path)
    assert "ELEVENLABS_API_KEY=sk-previous" in keys_path.read_text()
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "ELEVENLABS_API_KEY" in m
        and "contains a character no HTTP header may carry" in m
        for m in messages
    )
    assert all(bad_value.strip() not in m for m in messages)


def test_write_keys_env_no_sudo_required_note(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """Header must tell users they can edit without sudo."""
    keys_path = tmp_path / "keys.env"
    writer.write({"TTS_PROVIDER": "say"}, keys_path)
    content = keys_path.read_text()
    assert "no sudo" in content.lower()


def test_write_keys_env_rejects_control_chars_in_value(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """Values containing newlines or NUL bytes are dropped, not written."""
    keys_path = tmp_path / "keys.env"
    writer.write(
        {
            "OPENAI_API_KEY": "sk-legit",
            "ELEVENLABS_API_KEY": "sk-evil\nAWS_ACCESS_KEY_ID=injected",
        },
        keys_path,
    )
    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-legit" in content
    assert "injected" not in content
    assert "ELEVENLABS_API_KEY" not in content


def test_write_keys_env_handles_unreadable_existing_file(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """Non-UTF-8 bytes in an existing keys.env do not crash the install."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_bytes(b"\xff\xfe garbage \x00\x01\x02")

    writer.write(
        {"OPENAI_API_KEY": "sk-clean", "TTS_PROVIDER": "openai"},
        keys_path,
    )

    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-clean" in content
    assert "TTS_PROVIDER=openai" in content
    assert "garbage" not in content
    mode = stat_mod.S_IMODE(keys_path.stat().st_mode)
    assert mode == 0o600


def test_write_keys_env_handles_unreadable_existing_file_oserror(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """OSError (e.g. permission denied) during read_text is non-fatal."""
    keys_path = tmp_path / "keys.env"
    keys_path.write_text("OPENAI_API_KEY=stale\n")
    keys_path.chmod(0o000)
    try:
        writer.write({"OPENAI_API_KEY": "sk-new"}, keys_path)
    finally:
        keys_path.chmod(0o600)

    content = keys_path.read_text()
    assert "OPENAI_API_KEY=sk-new" in content
    assert "stale" not in content


def test_write_keys_env_never_exposes_world_readable_state(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """keys.env must never exist at wider-than-0600 permissions."""
    old_umask = os.umask(0o002)
    try:
        keys_path = tmp_path / "keys.env"
        writer.write({"OPENAI_API_KEY": "sk-test"}, keys_path)
        mode = stat_mod.S_IMODE(keys_path.stat().st_mode)
        assert mode == 0o600, f"keys.env mode is {oct(mode)} under umask 0002"
    finally:
        os.umask(old_umask)


def test_write_keys_env_tightens_parent_dir(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """``write()`` enforces mode 0700 on the parent dir."""
    state_root = tmp_path / ".punt-labs" / "vox"
    state_root.mkdir(parents=True)
    state_root.chmod(0o755)
    assert stat_mod.S_IMODE(state_root.stat().st_mode) == 0o755

    keys_path = state_root / "keys.env"
    writer.write({"OPENAI_API_KEY": "sk-test"}, keys_path)

    mode = stat_mod.S_IMODE(state_root.stat().st_mode)
    assert mode == 0o700, f"parent dir mode is {oct(mode)} after write; expected 0o700"


def test_write_keys_env_rejects_directory_at_keys_path(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """A directory at ``keys_path`` aborts the install with SystemExit."""
    keys_path = tmp_path / "keys.env"
    keys_path.mkdir()

    with pytest.raises(SystemExit, match="not a regular file"):
        writer.write({"OPENAI_API_KEY": "sk-test"}, keys_path)

    assert keys_path.is_dir()


def test_write_keys_env_rejects_symlink_at_keys_path(
    writer: KeysEnvWriter, tmp_path: Path
) -> None:
    """A symlink at ``keys_path`` aborts the install with SystemExit."""
    real_target = tmp_path / "elsewhere.txt"
    real_target.write_text("OPENAI_API_KEY=via-symlink\n")
    keys_path = tmp_path / "keys.env"
    keys_path.symlink_to(real_target)

    with pytest.raises(SystemExit, match="not a regular file"):
        writer.write({"OPENAI_API_KEY": "sk-test"}, keys_path)

    assert keys_path.is_symlink()
    assert real_target.read_text() == "OPENAI_API_KEY=via-symlink\n"
