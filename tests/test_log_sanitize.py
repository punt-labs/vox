"""Tests for the shared log sanitizer (src/punt_vox/log_sanitize.py)."""

from __future__ import annotations

from punt_vox.log_sanitize import SANITIZER, LogSanitizer

_LS = chr(0x2028)  # LINE SEPARATOR -- built by ordinal so the source has no raw copy
_PS = chr(0x2029)  # PARAGRAPH SEPARATOR


class TestLogSanitizer:
    """Escape control and line-breaking code points to visible, one-line text."""

    def test_singleton_is_a_log_sanitizer(self) -> None:
        assert isinstance(SANITIZER, LogSanitizer)

    def test_plain_text_is_unchanged(self) -> None:
        assert SANITIZER.escape("provider=eleven voice=rachel chars=42") == (
            "provider=eleven voice=rachel chars=42"
        )

    def test_newline_cannot_forge_a_second_line(self) -> None:
        escaped = SANITIZER.escape("player crashed\nFATAL forged")
        assert escaped == "player crashed\\nFATAL forged"
        assert "\n" not in escaped  # the record stays exactly one physical line

    def test_carriage_return_and_tab_get_short_escapes(self) -> None:
        assert SANITIZER.escape("a\rb\tc") == "a\\rb\\tc"

    def test_other_c0_controls_and_del_become_hex(self) -> None:
        # BEL, NUL, and DEL are the sharp edges: raw on a terminal they ring the
        # bell, truncate C strings, and move the cursor. They must stay visible.
        assert SANITIZER.escape("x\x07\x00\x7fy") == "x\\x07\\x00\\x7fy"

    def test_unicode_line_separators_become_visible_escapes(self) -> None:
        # NEL, LINE SEPARATOR, PARAGRAPH SEPARATOR -- str.splitlines treats each
        # as a break, so a Unicode-aware viewer could render a smuggled one as a
        # second visual record. Escape them too.
        assert SANITIZER.escape(f"a\x85b{_LS}c{_PS}d") == "a\\u0085b\\u2028c\\u2029d"

    def test_escaped_output_has_no_line_breaks(self) -> None:
        smuggled = f"s\n\r\x85{_LS}{_PS}\x00"
        assert SANITIZER.escape(smuggled).splitlines() == [SANITIZER.escape(smuggled)]
