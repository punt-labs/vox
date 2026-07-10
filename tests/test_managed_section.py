"""Tests for the managed ``@``-import block grammar (``ManagedSection``)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from punt_vox.managed_section import ManagedSection

_OPEN = "<!-- punt:mandatory-reading -->"
_CLOSE = "<!-- /punt:mandatory-reading -->"
_HEADER = "## Tool Guidance (auto-loaded)"
_VOX = "@~/.punt-labs/vox/CLAUDE.md"

_Update = Callable[[frozenset[str]], frozenset[str]]


def _reconcile(text: str, update: _Update) -> str:
    section = ManagedSection()
    kept, imports = section.parse(text)
    return section.render(kept, update(imports))


def test_parse_empty_text_yields_no_imports() -> None:
    kept, imports = ManagedSection().parse("")
    assert kept == []
    assert imports == frozenset()


def test_parse_extracts_imports_and_drops_header() -> None:
    text = f"# rules\n\n{_OPEN}\n{_HEADER}\n\n{_VOX}\n{_CLOSE}\n"
    kept, imports = ManagedSection().parse(text)
    assert imports == frozenset({_VOX})
    assert _HEADER not in "".join(kept)
    assert "# rules" in "".join(kept)


def test_render_sorts_imports_deterministically() -> None:
    section = ManagedSection()
    out = section.render([], frozenset({"@~/z.md", "@~/a.md", _VOX}))
    lines = [ln for ln in out.splitlines() if ln.startswith("@")]
    assert lines == [_VOX, "@~/a.md", "@~/z.md"]


def test_render_no_imports_returns_body_unchanged() -> None:
    body = "# rules\n\nkeep me\n"
    assert ManagedSection().render([body], frozenset()) == body


@pytest.mark.parametrize(
    "original",
    [
        "# rules\n\ncontent\n",
        "# rules\n\ncontent",  # no final newline
        "",  # empty file
        "# a\n\n\n",  # trailing blanks
    ],
)
def test_register_then_prune_round_trip_is_identity(original: str) -> None:
    # The section-level analogue of prune(register(x)) == x: adding then
    # removing an import restores the user's content byte-for-byte.
    added = _reconcile(original, lambda imports: imports | {_VOX})
    restored = _reconcile(added, lambda imports: imports - {_VOX})
    assert restored == original


def test_fenced_marker_is_literal_not_a_delimiter() -> None:
    fenced = f"# doc\n\n```\n{_OPEN}\n{_VOX}\n{_CLOSE}\n```\n"
    kept, imports = ManagedSection().parse(fenced)
    # The fenced marker is not harvested; the block survives verbatim.
    assert imports == frozenset()
    assert "".join(kept) == fenced


def test_duplicate_sections_collapse_to_one_on_render() -> None:
    doubled = (
        f"{_OPEN}\n{_HEADER}\n\n@~/a.md\n{_CLOSE}\n\n"
        f"{_OPEN}\n{_HEADER}\n\n@~/b.md\n{_CLOSE}\n"
    )
    out = _reconcile(doubled, lambda imports: imports)
    assert out.count(_OPEN) == 1
    assert out.count(_CLOSE) == 1
    assert "@~/a.md" in out
    assert "@~/b.md" in out
