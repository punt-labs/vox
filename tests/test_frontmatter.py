"""Tests for punt_vox.frontmatter -- single-file YAML frontmatter I/O."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import logging
import multiprocessing
import os
import time
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import IO
from unittest.mock import patch

import pytest

from punt_vox.config_file import ConfigFile
from punt_vox.frontmatter import Frontmatter
from punt_vox.frontmatter_block import FrontmatterBlock
from punt_vox.sibling_lock import SiblingLock
from punt_vox.types_errors import ConfigValueError


def _concurrent_writer(
    path: Path,
    key: str,
    value: str,
    read: Event,
    release: Event,
    attempted: Event | None = None,
) -> None:
    original = Path.read_text
    original_flock = fcntl.flock

    def observed_flock(fd: int, operation: int) -> None:
        if attempted is not None and operation & fcntl.LOCK_EX:
            attempted.set()
        original_flock(fd, operation)

    def paused_read(
        self: Path, *, encoding: str | None = None, newline: str | None = None
    ) -> str:
        text = original(self, encoding=encoding, newline=newline)
        if self == path:
            read.set()
            assert release.wait(30), "writer was not released"
        return text

    with (
        patch.object(Path, "read_text", paused_read),
        patch("punt_vox.sibling_lock.fcntl.flock", observed_flock),
    ):
        Frontmatter(path).write_field(key, value)


def _wait_for_either(first: Event, second: Event, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not (first.is_set() or second.is_set()):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        first.wait(min(0.01, remaining))
    return True


@pytest.mark.parametrize("symlinked", [False, True])
def test_concurrent_writers_preserve_both_fields(
    tmp_path: Path, symlinked: bool
) -> None:
    path = tmp_path / ".punt-labs" / "vox" / "vox.md"
    path.parent.mkdir(parents=True)
    path.write_text('---\nvoice: "initial"\n---\n')
    second_path = tmp_path / "alias.md" if symlinked else path
    if symlinked:
        second_path.symlink_to(path)
    ctx = multiprocessing.get_context("spawn")
    first_read, second_read = ctx.Event(), ctx.Event()
    first_release, second_release = ctx.Event(), ctx.Event()
    second_attempted = ctx.Event()
    first = ctx.Process(
        target=_concurrent_writer,
        args=(path, "provider", "polly", first_read, first_release),
    )
    second = ctx.Process(
        target=_concurrent_writer,
        args=(
            second_path,
            "voice",
            "fin",
            second_read,
            second_release,
            second_attempted,
        ),
    )
    first.start()
    try:
        assert first_read.wait(10)
        second.start()
        # An unlocked writer reads the stale snapshot; a locked one waits.
        assert _wait_for_either(second_read, second_attempted, 10), (
            "second writer neither read nor attempted the lock"
        )
        first_release.set()
        first.join(10)
        assert first.exitcode == 0
        second_release.set()
        second.join(10)
        assert second.exitcode == 0
        assert Frontmatter(path).read_fields() == {"provider": "polly", "voice": "fin"}
    finally:
        first_release.set()
        second_release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(10)
                if process.is_alive():
                    process.kill()
                    process.join()


def _boom(self: Path, *_args: object, **_kwargs: object) -> str:
    """Stand-in for ``Path.read_text`` that fails as a permission fault would."""
    raise PermissionError("permission denied")


class TestRead:
    def test_missing_file_reads_empty(self, tmp_path: Path) -> None:
        assert Frontmatter(tmp_path / "vox.md").read_fields() == {}

    def test_missing_file_field_is_none(self, tmp_path: Path) -> None:
        assert Frontmatter(tmp_path / "vox.md").read_field("voice") is None

    def test_parses_non_empty_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\nnotify: ""\n---\n')
        fm = Frontmatter(path)
        assert fm.read_fields() == {"voice": "charlie"}
        assert fm.read_field("voice") == "charlie"
        assert fm.read_field("notify") is None

    def test_unreadable_file_reads_empty_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An existing-but-unreadable file degrades to defaults, not a crash."""
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\n---\n')
        monkeypatch.setattr(Path, "read_text", _boom)
        with caplog.at_level(logging.WARNING, logger="punt_vox.config_file"):
            assert Frontmatter(path).read_fields() == {}
        messages = [r.getMessage() for r in caplog.records]
        assert any("unreadable" in m and str(path) in m for m in messages)

    def test_unreadable_file_field_is_none_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\n---\n')
        monkeypatch.setattr(Path, "read_text", _boom)
        with caplog.at_level(logging.WARNING, logger="punt_vox.config_file"):
            assert Frontmatter(path).read_field("voice") is None
        assert any(str(path) in r.getMessage() for r in caplog.records)

    def test_strict_read_refuses_an_unclosed_fence_as_the_writer_does(
        self, tmp_path: Path
    ) -> None:
        """One rule for reader and writer: an unclosed file is not frontmatter."""
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "fin"\n', encoding="utf-8")
        with pytest.raises(OSError, match="no closing ---") as refused:
            Frontmatter(path).read_field("voice", strict=True)
        assert not isinstance(refused.value, ValueError)
        assert refused.value.errno == errno.EBADMSG
        assert refused.value.filename == str(path)
        # The default read stays lenient: a hook subprocess reads what it can.
        assert Frontmatter(path).read_field("voice") == "fin"

    def test_strict_read_of_a_missing_file_is_absent(self, tmp_path: Path) -> None:
        assert Frontmatter(tmp_path / "vox.md").read_field("voice", strict=True) is None


class TestWrite:
    def test_read_only_config_fails_the_write(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        original = '---\nvoice: "initial"\n---\n'
        path.write_text(original)
        path.chmod(0o400)
        with pytest.raises(PermissionError):
            Frontmatter(path).write_field("voice", "fin")
        assert path.read_text() == original
        assert list(tmp_path.iterdir()) == [path]

    def test_a_short_write_leaves_the_original_bytes_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running out of space mid-write never truncates the live config.

        The write lands on a sibling temp file and is renamed over the
        config only once it is complete, so a disk-full failure leaves every
        setting the file held and the error reaches the caller.
        """
        path = tmp_path / "vox.md"
        original = '---\nvoice: "initial"\nprovider: "polly"\n---\n'
        path.write_text(original, encoding="utf-8")
        real_open = io.open

        class ShortWrite(io.TextIOWrapper):
            """A text handle whose first write runs out of space."""

            def write(self, _text: str, /) -> int:
                raise OSError(errno.ENOSPC, "No space left on device")

        # The signature ``Path.open`` and ``os.fdopen`` both call ``io.open``
        # with, so the stand-in sits under the old in-place write and the
        # replacement alike; ``None`` is each keyword's own default.
        def open_then_fail(
            file: str | Path | int,
            mode: str,
            buffering: int,
            encoding: str | None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> IO[str]:
            if "w" not in mode:
                return real_open(file, mode, buffering, encoding, errors, newline)
            binary = real_open(file, "wb", buffering)
            return ShortWrite(binary, encoding, errors, newline)

        monkeypatch.setattr(io, "open", open_then_fail)
        with pytest.raises(OSError, match="No space left") as failed:
            Frontmatter(path).write_field("voice", "fin")
        assert failed.value.errno == errno.ENOSPC
        assert path.read_text(encoding="utf-8") == original
        assert sorted(tmp_path.iterdir()) == [path]

    def test_checked_write_refuses_an_unfenced_file_as_a_format_fault(
        self, tmp_path: Path
    ) -> None:
        """The refusal is an OSError naming the file: a handler faults, not errors."""
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "fin"\n')
        with pytest.raises(OSError, match="no closing ---") as refused:
            Frontmatter(path).write_fields({"voice": "charlie"}, check=lambda _: None)
        assert not isinstance(refused.value, ValueError)
        assert refused.value.filename == str(path)
        assert refused.value.strerror == "no closing ---"
        assert path.read_text() == '---\nvoice: "fin"\n'

    def test_empty_updates(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        Frontmatter(path).write_fields({})
        assert Frontmatter(path).read_fields() == {}

    def test_failed_write_releases_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "vox.md"
        original = '---\nvoice: "initial"\n---\n'
        path.write_text(original)
        with monkeypatch.context() as scoped:
            scoped.setattr(ConfigFile, "replace", _boom)
            with pytest.raises(PermissionError):
                Frontmatter(path).write_field("provider", "polly")
        assert path.read_text() == original
        Frontmatter(path).write_field("voice", "fin")
        assert Frontmatter(path).read_field("voice") == "fin"

    @pytest.mark.parametrize("filename", ["vox.md", "vox.local.md"])
    def test_config_lock_is_in_the_local_zone(
        self, tmp_path: Path, filename: str
    ) -> None:
        directory = tmp_path / ".punt-labs" / "vox"
        Frontmatter(directory / filename).write_field("voice", "fin")
        assert list(directory.iterdir()) == [directory / filename]
        lock = tmp_path / ".punt-labs" / "local" / "vox" / f"{filename}.lock"
        assert lock.is_file()

    def test_global_config_lock_is_in_home_local_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        path = tmp_path / ".punt-labs" / "vox" / "vox.md"
        Frontmatter(path).write_field("voice", "fin")
        assert (tmp_path / ".punt-labs/local/vox/vox.md.lock").is_file()

    def test_custom_configs_use_distinct_home_locks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("VOX_STATE_DIR", str(tmp_path / "other state"))
        first = Frontmatter(tmp_path / "one" / "vox.md")
        second = Frontmatter(tmp_path / "two" / "vox.md")
        first.write_field("voice", "fin")
        second.write_field("voice", "roger")
        locks = tmp_path / "home/.punt-labs/vox/run/config-locks"
        assert len(list(locks.iterdir())) == 2
        for config in (first, second):
            digest = hashlib.sha256(str(config.path.resolve()).encode()).hexdigest()
            assert (locks / digest).is_file()
        assert list(first.path.parent.iterdir()) == [first.path]
        assert list(second.path.parent.iterdir()) == [second.path]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-555 dir")
    def test_read_only_directory_refuses_the_write_and_keeps_the_file(
        self, tmp_path: Path
    ) -> None:
        """The replacement lands by rename, which needs the directory.

        A writable file in a directory the user locked is refused whole, the
        same answer as any other unwritable location, and nothing is left
        behind in the directory.
        """
        directory = tmp_path / "config"
        directory.mkdir()
        path = directory / "vox.md"
        original = '---\nvoice: "initial"\n---\n'
        path.write_text(original)
        directory.chmod(0o555)
        try:
            with pytest.raises(PermissionError):
                Frontmatter(path).write_field("voice", "fin")
            assert path.read_text() == original
            assert list(directory.iterdir()) == [path]
        finally:
            directory.chmod(0o755)

    def test_new_config_honors_normal_creation_permissions(
        self, tmp_path: Path
    ) -> None:
        control = tmp_path / "control"
        control.write_text("")
        path = tmp_path / "vox.md"
        Frontmatter(path).write_field("voice", "fin")
        assert path.stat().st_mode == control.stat().st_mode

    def test_lock_error_does_not_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "vox.md"
        monkeypatch.setattr(SiblingLock, "_acquire", _boom)
        with pytest.raises(PermissionError):
            Frontmatter(path).write_field("provider", "polly")
        assert not path.exists()

    @pytest.mark.parametrize("existing", [False, True])
    def test_refused_write_leaves_nothing_on_disk(
        self, tmp_path: Path, existing: bool
    ) -> None:
        path = tmp_path / ".punt-labs" / "vox" / "vox.md"
        if existing:
            path.parent.mkdir(parents=True)
            path.write_text('---\nvoice: "initial"\n---\n')
        before = sorted(tmp_path.rglob("*"))

        def refuse(fields: dict[str, str]) -> None:
            msg = "refused"
            raise ValueError(msg)

        with pytest.raises(ValueError, match="refused"):
            Frontmatter(path).write_fields({"voice": "fin"}, check=refuse)
        assert sorted(tmp_path.rglob("*")) == before

    def test_refused_malformed_write_leaves_nothing_on_disk(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".punt-labs" / "vox" / "vox.md"
        path.parent.mkdir(parents=True)
        path.write_text('---\nprovider: "polly"\n')
        before = sorted(tmp_path.rglob("*"))

        with pytest.raises(OSError, match="no closing ---"):
            Frontmatter(path).write_fields({"voice": "fin"}, check=lambda _: None)
        assert sorted(tmp_path.rglob("*")) == before

    def test_existing_lock_defers_the_check_to_the_lock(self, tmp_path: Path) -> None:
        # A lock file means a writer may be mid-rewrite, so an unlocked read
        # could see a torn file; only the check under the lock may run.
        path = tmp_path / ".punt-labs" / "vox" / "vox.md"
        lock = tmp_path / ".punt-labs" / "local" / "vox" / "vox.md.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        seen: list[dict[str, str]] = []

        Frontmatter(path).write_fields({"voice": "fin"}, check=seen.append)
        assert seen == [{}]

    def test_lock_taken_during_the_early_read_defers_the_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A first writer may take the lock while the early read runs, so the
        # read must come before the lock-file test, not after it.
        path = tmp_path / ".punt-labs" / "vox" / "vox.md"
        lock = tmp_path / ".punt-labs" / "local" / "vox" / "vox.md.lock"
        read_block = ConfigFile.block

        def read_while_a_writer_starts(
            file: ConfigFile,
        ) -> FrontmatterBlock | None:  # None: no config file.
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.touch()
            return read_block(file)

        monkeypatch.setattr(ConfigFile, "block", read_while_a_writer_starts)
        seen: list[dict[str, str]] = []

        Frontmatter(path).write_fields({"voice": "fin"}, check=seen.append)
        assert seen == [{}]

    def test_write_field_round_trips(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_field("voice", "fin")
        assert fm.read_field("voice") == "fin"
        assert (
            fm.path.read_bytes()
            == os.linesep.join(("---", 'voice: "fin"', "---", "")).encode()
        )

    @pytest.mark.parametrize("key", ["sample-rate", "sample.rate"])
    def test_custom_field_round_trips(self, tmp_path: Path, key: str) -> None:
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_field(key, "16000")
        assert fm.read_field(key) == "16000"
        assert fm.read_fields() == {key: "16000"}

    def test_unfenced_field_with_nonbreaking_spaces_edits_in_place(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "vox.md"
        tail = '\nnotify: "c"\n\n# Notes\n'
        path.write_text('voice:\u00a0"fin"\u00a0' + tail, encoding="utf-8")
        fm = Frontmatter(path)
        assert fm.read_field("voice") == "fin"
        fm.write_field("voice", "charlie")
        assert path.read_text(encoding="utf-8") == 'voice: "charlie"' + tail

    @pytest.mark.parametrize("newline", ["\r\n", "\r"], ids=["crlf", "cr"])
    def test_file_reads_and_edits_only_its_header(
        self, tmp_path: Path, newline: str
    ) -> None:
        path = tmp_path / "vox.md"
        body = '# Notes\nvoice: "body"\n'
        text = '---\nvoice: "fin"\n---\n' + body
        path.write_bytes(text.replace("\n", newline).encode())
        fm = Frontmatter(path)
        assert fm.read_field("voice") == "fin"
        assert fm.read_fields() == {"voice": "fin"}
        fm.write_field("voice", "charlie")
        assert path.read_text(encoding="utf-8") == '---\nvoice: "charlie"\n---\n' + body
        assert path.read_bytes() == (
            ('---\nvoice: "charlie"\n---\n' + body).replace("\n", newline).encode()
        )

    @pytest.mark.parametrize(
        "newline", [b"\n", b"\r\n", b"\r"], ids=["lf", "crlf", "cr"]
    )
    def test_insert_field_preserves_file_bytes(
        self, tmp_path: Path, newline: bytes
    ) -> None:
        path = tmp_path / "vox.md"
        header = newline.join((b"---", b'voice: "fin"', b""))
        # The body may use a different newline style and need not end in one.
        tail = b"---" + newline + "# Notes\nRésumé".encode()
        path.write_bytes(header + tail)

        Frontmatter(path).write_field("notify", "y")

        assert path.read_bytes() == header + b'notify: "y"' + newline + tail

    @pytest.mark.parametrize("fenced", [False, True])
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("fin", "fin"),
            ('"fin"', "fin"),
            ("'Ready: now'", "'Ready: now'"),
            ("\u00a0fin", "fin"),
        ],
    )
    def test_single_indented_scalar_edits_in_place(
        self, tmp_path: Path, fenced: bool, value: str, expected: str
    ) -> None:
        path = tmp_path / "vox.md"
        opening = "---\n" if fenced else ""
        tail = 'notify: "c"\n' + ("---\n" if fenced else "") + "# Notes\n"
        path.write_text(f"{opening}voice:\n  {value}\n{tail}", encoding="utf-8")
        fm = Frontmatter(path)
        assert fm.read_field("voice") == expected
        assert fm.read_fields() == {"voice": expected, "notify": "c"}
        fm.write_field("voice", "charlie")
        assert path.read_text(encoding="utf-8") == f'{opening}voice: "charlie"\n{tail}'

    def test_write_creates_parent_dir(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "a" / "b" / "vox.md")
        fm.write_field("voice", "fin")
        assert fm.read_field("voice") == "fin"

    def test_write_fields_updates_in_place(self, tmp_path: Path) -> None:
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_fields({"voice": "fin", "notify": "y"})
        fm.write_field("voice", "roger")
        assert fm.read_field("voice") == "roger"
        assert fm.read_field("notify") == "y"

    def test_mood_text_is_logged_by_length_not_content(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``vibe``/``vibe_tags`` log their length; the mood text never lands.

        The value is expressive, agent-authored content -- persisting it verbatim
        in a durable log leaks it. The per-field detail is DEBUG now (one INFO
        summary per write); the mood text must never appear at any level.
        """
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_fields({"voice": "fin"})  # create the file so the next write logs
        with caplog.at_level(logging.DEBUG, logger="punt_vox.frontmatter"):
            fm.write_fields({"vibe": "[frustrated] [weary]", "voice": "roger"})
        messages = [r.getMessage() for r in caplog.records]
        assert any("vibe = <20 chars>" in m for m in messages)
        assert not any("frustrated" in m for m in messages)  # content never logged
        assert any("voice = 'roger'" in m for m in messages)  # non-mood key unchanged
        # Exactly one INFO summary; the per-field lines are DEBUG.
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        assert "updated 2 field(s)" in infos[0].getMessage()

    def test_malformed_file_rewritten_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "vox.md"
        path.write_text("no frontmatter fence here\n")
        with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter"):
            Frontmatter(path).write_field("voice", "fin")
        assert Frontmatter(path).read_field("voice") == "fin"
        assert any("Malformed config" in r.getMessage() for r in caplog.records)

    def test_an_unreadable_existing_file_fails_the_write_instead_of_replacing_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The write path reads strictly, unlike the read path.

        A file that exists but will not open is a fault: rewriting it whole
        from the updates alone would silently destroy every field this write
        was meant to preserve. The caller hears the fault instead.
        """
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\nnotify: "y"\n---\n')
        monkeypatch.setattr(Path, "read_text", _boom)
        with pytest.raises(PermissionError):
            Frontmatter(path).write_field("voice", "fin")

    def test_an_unfenced_file_keeps_a_field_it_already_holds(
        self, tmp_path: Path
    ) -> None:
        """A field already present is edited where it sits, fence or no fence."""
        path = tmp_path / "vox.md"
        path.write_text('voice: "charlie"\nsome prose below\n')
        Frontmatter(path).write_field("voice", "fin")
        assert Frontmatter(path).read_field("voice") == "fin"
        assert "some prose below" in path.read_text()

    def test_a_rule_in_the_prose_does_not_take_the_new_field(
        self, tmp_path: Path
    ) -> None:
        """A new field lands in the frontmatter, never above a body rule."""
        path = tmp_path / "vox.md"
        path.write_text('---\nvoice: "charlie"\n---\n\n# Notes\n\n---\n\ntail\n')
        Frontmatter(path).write_field("notify", "y")
        assert path.read_text() == (
            '---\nvoice: "charlie"\nnotify: "y"\n---\n\n# Notes\n\n---\n\ntail\n'
        )

    def test_an_unfenced_file_with_a_rule_is_rewritten_with_a_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Prose carrying a ``---`` rule is not frontmatter to edit in place.

        Reading that rule as a closing fence would insert the field into the
        middle of the body, where no reader of the frontmatter would find it.
        """
        path = tmp_path / "vox.md"
        path.write_text("# Notes\n\nabove\n\n---\n\nbelow\n")
        with caplog.at_level(logging.WARNING, logger="punt_vox.frontmatter"):
            Frontmatter(path).write_field("voice", "fin")
        assert path.read_text() == '---\nvoice: "fin"\n---\n'
        assert any("Malformed config" in r.getMessage() for r in caplog.records)

    def test_write_fields_validates_before_touching_file(self, tmp_path: Path) -> None:
        """A corrupting value is rejected and no file is created."""
        path = tmp_path / "vox.md"
        fm = Frontmatter(path)
        with pytest.raises(ConfigValueError, match="double-quotes"):
            fm.write_fields({"voice": "fin", "vibe": 'I"m tired'})
        assert not path.exists()

    def test_write_field_rejects_double_quote(self, tmp_path: Path) -> None:
        """The single-field path routes through the same validation.

        The specific type is what a caller of the write path sees, and what
        lets it tell a value it cannot store from a request it cannot read.
        """
        path = tmp_path / "vox.md"
        with pytest.raises(ConfigValueError, match="double-quotes"):
            Frontmatter(path).write_field("vibe", 'say "hi"')
        assert not path.exists()

    def test_write_field_accepts_apostrophe_round_trips(self, tmp_path: Path) -> None:
        """Apostrophes are safe -- mood text like ``I'm tired`` survives."""
        fm = Frontmatter(tmp_path / "vox.md")
        fm.write_field("vibe", "I'm tired")
        assert fm.read_field("vibe") == "I'm tired"


class TestValidateValue:
    @pytest.mark.parametrize("bad", ["a\nb", "a\rb"])
    def test_rejects_newlines(self, bad: str) -> None:
        with pytest.raises(ConfigValueError, match="must not contain newlines"):
            Frontmatter.validate_value(bad)

    @pytest.mark.parametrize("bad", ['I"m tired', 'say "hi"', '"'])
    def test_rejects_double_quotes(self, bad: str) -> None:
        with pytest.raises(ConfigValueError, match="must not contain double-quotes"):
            Frontmatter.validate_value(bad)

    @pytest.mark.parametrize("bad", ["a\nb", 'say "hi"'])
    def test_the_refusal_is_still_a_value_error(self, bad: str) -> None:
        """The specific type is additive: an existing ``except ValueError``
        anywhere in the tree keeps catching what it always caught."""
        with pytest.raises(ValueError, match="config values must not"):
            Frontmatter.validate_value(bad)

    def test_accepts_plain_value(self) -> None:
        Frontmatter.validate_value("charlie")  # no raise

    def test_accepts_apostrophe(self) -> None:
        Frontmatter.validate_value("I'm tired")  # no raise


class TestPath:
    def test_path_property_returns_backing_path(self, tmp_path: Path) -> None:
        path = tmp_path / "vox.md"
        assert Frontmatter(path).path == path
