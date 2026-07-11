"""Git queries the ratchet needs: base resolution, diffs, and blob reads."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

_TIMEOUT = 10


class GitError(Exception):
    """A git command the ratchet depends on failed.

    Raised instead of degrading to a benign default, so an enforcement gate
    fails closed: a failed ``git diff`` can never masquerade as "no changes".
    """


@dataclass(frozen=True, slots=True)
class Diff:
    """Files changed across a commit range, with rename provenance.

    ``renames`` maps a new path to the old path it was renamed from, so a
    renamed file can inherit its predecessor's baseline entry (S8).
    """

    touched: frozenset[str]
    renames: dict[str, str] = field(default_factory=dict)

    def python_files(self) -> frozenset[str]:
        """Return the touched paths that are Python modules."""
        return frozenset(p for p in self.touched if p.endswith(".py"))


class GitRepo:
    """Answer the ratchet's git questions, or degrade to ``None`` outside git."""

    _root: Path | None

    BASELINE_FILE: str = ".oo-baseline.json"
    AUDIT_FILE: str = ".oo-audit.jsonl"

    def __new__(cls, start: Path | None = None) -> Self:
        self = super().__new__(cls)
        self._root = cls._discover_root(start if start is not None else Path.cwd())
        return self

    @property
    def root(self) -> Path | None:
        """Return the repository root, or ``None`` when not inside a repo."""
        return self._root

    @property
    def available(self) -> bool:
        """Return whether git commands can run against a repository."""
        return self._root is not None

    @classmethod
    def _discover_root(cls, start: Path) -> Path | None:
        out = cls._run(["git", "rev-parse", "--show-toplevel"], cwd=start)
        return Path(out.strip()) if out is not None else None

    @staticmethod
    def _run(args: list[str], cwd: Path | None) -> str | None:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                cwd=cwd,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def _git(self, args: list[str]) -> str | None:
        return self._run(["git", *args], cwd=self._root)

    def short_head(self) -> str | None:
        """Return the abbreviated HEAD commit hash."""
        out = self._git(["rev-parse", "--short", "HEAD"])
        return out.strip() if out is not None else None

    def resolve_ref(self, ref: str) -> str | None:
        """Return the full commit hash for a ref, or ``None`` if unresolvable."""
        out = self._git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        return out.strip() if out else None

    def merge_base(self, left: str, right: str) -> str | None:
        """Return the merge-base commit of two refs, or ``None``."""
        out = self._git(["merge-base", left, right])
        return out.strip() if out else None

    def resolve_base(self, base_ref: str | None) -> str | None:
        """Resolve the comparison base: explicit ref, else merge-base of main.

        Returns the base commit hash, or ``None`` when no base can be
        determined (caller decides bootstrap vs. hard-fail).
        """
        if base_ref is not None:
            return self.resolve_ref(base_ref)
        return self.merge_base("origin/main", "HEAD")

    def diff(self, base: str) -> Diff:
        """Return files changed from ``base`` to the work tree, with renames.

        Diffing against the work tree (not ``base..HEAD``) keeps the touched
        set consistent with the scored tree: identical to ``base..HEAD`` in a
        clean checkout (CI), and inclusive of a developer's tracked
        pre-commit edits locally.
        """
        out = self._git(["diff", "--name-status", "-M", base])
        if out is None:
            msg = f"git diff against {base} failed"
            raise GitError(msg)
        touched: set[str] = set()
        renames: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith(("R", "C")) and len(parts) >= 3:
                old, new = parts[1], parts[2]
                touched.add(new)
                if status.startswith("R"):
                    renames[new] = old
            elif len(parts) >= 2:
                touched.add(parts[1])
        return Diff(frozenset(touched), renames)

    def show_baseline(self, ref: str) -> dict[str, dict[str, float]] | None:
        """Return the baseline JSON committed at ``ref``, or ``None``.

        ``None`` means the baseline blob does not exist at that ref — the
        first-adoption case, where there is nothing to compare against.
        """
        out = self._git(["show", f"{ref}:{self.BASELINE_FILE}"])
        if out is None:
            return None
        try:
            parsed: dict[str, dict[str, float]] = json.loads(out)
        except json.JSONDecodeError as exc:
            msg = f"corrupt baseline blob at {ref}:{self.BASELINE_FILE}: {exc}"
            raise GitError(msg) from exc
        return parsed

    def show_audit(self, ref: str) -> str | None:
        """Return the raw audit-log text committed at ``ref``, or ``None``.

        ``None`` means the audit blob does not exist at that ref; the caller
        treats it as an empty history so every current relaxation counts as new.
        """
        return self._git(["show", f"{ref}:{self.AUDIT_FILE}"])
