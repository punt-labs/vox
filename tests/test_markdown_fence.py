"""Tests for the Markdown code-fence scanner (``Fence``)."""

from __future__ import annotations

from punt_vox.markdown_fence import Fence


def test_fresh_fence_is_outside() -> None:
    assert Fence().inside is False


def test_backtick_fence_opens_and_closes() -> None:
    fence = Fence()
    assert fence.feed("```") is True  # opening delimiter
    assert fence.inside is True
    assert fence.feed("code") is False  # content line
    assert fence.inside is True
    assert fence.feed("```") is True  # closing delimiter
    assert fence.inside is False


def test_tilde_fence_is_independent_of_backtick() -> None:
    fence = Fence()
    assert fence.feed("~~~") is True
    assert fence.inside is True
    # A backtick run does not close a tilde fence.
    assert fence.feed("```") is False
    assert fence.inside is True
    assert fence.feed("~~~") is True
    assert fence.inside is False


def test_info_string_opens_but_bare_run_closes() -> None:
    fence = Fence()
    # ```python opens a fence...
    assert fence.feed("```python") is True
    # ...another info-string line does not close it (info strings never close).
    assert fence.feed("```markdown") is False
    assert fence.inside is True
    # A bare run of the fence char closes.
    assert fence.feed("```") is True
    assert fence.inside is False


def test_content_outside_fence_is_not_a_delimiter() -> None:
    fence = Fence()
    assert fence.feed("plain text") is False
    assert fence.inside is False


def test_longer_run_still_matches() -> None:
    fence = Fence()
    assert fence.feed("````") is True  # four backticks open
    assert fence.feed("````") is True  # bare run closes
    assert fence.inside is False
