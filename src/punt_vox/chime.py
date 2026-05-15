"""Chime asset resolution for notification signals."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["resolve_chime_path"]

_SIGNAL_CHIMES: dict[str, str] = {
    "tests-pass": "chime_tests_pass.mp3",
    "tests-fail": "chime_tests_fail.mp3",
    "lint-pass": "chime_lint_pass.mp3",
    "lint-fail": "chime_lint_fail.mp3",
    "git-push-ok": "chime_git_push_ok.mp3",
    "merge-conflict": "chime_merge_conflict.mp3",
    "git-commit": "chime_git_commit.mp3",
    "pr-created": "chime_pr_created.mp3",
}

_DEFAULT_CHIME = "chime_done.mp3"


def _resolve_assets_dir() -> Path | None:
    """Find the assets directory.

    Checks ``CLAUDE_PLUGIN_ROOT`` env var first (set by Claude Code
    for plugin processes), then falls back to the ``assets/`` subpackage
    next to this file (works for both editable and installed packages).
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        candidate = Path(plugin_root) / "assets"
        if candidate.is_dir():
            return candidate

    # Package-relative: chime.py sits alongside punt_vox/assets/
    candidate = Path(__file__).resolve().parent / "assets"
    if candidate.is_dir():
        return candidate

    return None


def resolve_chime_path(
    signal: str | None = None, *, mood: str = "neutral"
) -> Path | None:
    """Find the chime asset for *signal* and *mood*.

    Signals (e.g. ``"tests-pass"``) are mapped to filenames via
    ``_SIGNAL_CHIMES`` (e.g. ``"tests-pass"`` -> ``"chime_tests_pass.mp3"``).
    The ``.mp3`` suffix is stripped to get a stem, then candidates are
    built from that stem.

    Resolution chain (first existing file wins):
    1. ``{stem}_{mood}.mp3`` -- mood-specific signal chime
    2. ``{stem}.mp3``        -- neutral signal chime
    3. ``chime_done_{mood}.mp3`` -- mood-specific default
    4. ``chime_done.mp3``        -- neutral default

    When *mood* is ``"neutral"``, steps 1 and 3 are skipped (neutral
    files have no mood suffix).
    """
    assets_dir = _resolve_assets_dir()
    if assets_dir is None:
        return None

    candidates: list[str] = []

    if signal is not None:
        base = _SIGNAL_CHIMES.get(signal)
        if base:
            stem = base.removesuffix(".mp3")
            if mood != "neutral":
                candidates.append(f"{stem}_{mood}.mp3")
            candidates.append(base)

    # Default fallback
    default_stem = _DEFAULT_CHIME.removesuffix(".mp3")
    if mood != "neutral":
        candidates.append(f"{default_stem}_{mood}.mp3")
    candidates.append(_DEFAULT_CHIME)

    for filename in candidates:
        candidate = assets_dir / filename
        if candidate.exists():
            return candidate

    return None
