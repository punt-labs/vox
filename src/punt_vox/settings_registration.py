"""Additively register vox's repo-scoped permission rules in settings.json.

``tool-enable-disable.md`` § 2.8 lets ``enable`` touch one file outside vox's own
subtree -- ``<repo>/.claude/settings.json`` -- and only additively. The
deterministic entry set (:data:`punt_vox.permission_rules.VOX_RULES`) is vox's
identity in that file:
a broad ``permissions.allow`` rule per surface (CLI, MCP, skills) plus exact
``permissions.ask`` carve-outs for the unsafe verbs, so an enabled repo never
prompts for vox's safe tools. Nothing is granted outside the
repo, and the ``vox-dev`` rules are left to maintainers to add by hand.
:class:`SettingsRegistration` adds the missing members with the
order-preserving merge of [permissions.md § 6] and removes them by exact
value-match, both under the shared sibling lock (``.settings.json.punt-import.lock``)
because the file is read-modified-written by other tools and invocations too.
``.claude/`` is opened once without following a symlink and held across the lock
and the whole read-modify-write (:class:`~punt_vox.pinned_directory.PinnedDirectory`),
so a ``.claude`` swapped for a symlink while the lock is awaited is never
written through.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.permission_rules import VOX_RULES
from punt_vox.pinned_directory import PinnedDirectory
from punt_vox.settings_document import SettingsDocument
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = ["SettingsRegistration"]

# Strip the broad flag rule on both enable and disable: it prompts even for
# read-only invocations such as ``vox --version``.
_LEGACY_RULES: dict[str, tuple[str, ...]] = {"ask": ("Bash(vox -*)",)}


@final
class SettingsRegistration:
    """Register or deregister a deterministic permission-entry set in a settings file.

    Bind the settings path and the rule tiers at construction. :meth:`register`
    appends the missing entries to each ``permissions.<tier>`` preserving order;
    :meth:`deregister` removes every exact match. Both take the sibling lock for
    the whole read-modify-write, so a parallel ``enable`` never loses an update.
    """

    __slots__ = ("_lock", "_path", "_rules")

    _path: Path
    _lock: SiblingLock
    _rules: Mapping[str, tuple[str, ...]]

    def __new__(
        cls, settings_path: Path, rules: Mapping[str, tuple[str, ...]] = VOX_RULES
    ) -> Self:
        """Create a settings registration."""
        self = super().__new__(cls)
        self._path = settings_path
        self._lock = SiblingLock(settings_path)
        self._rules = rules
        return self

    @property
    def rules(self) -> Mapping[str, tuple[str, ...]]:
        """Return the rules this instance owns, keyed by ``permissions`` tier."""
        return self._rules

    def register(self) -> bool:
        """Add each owned rule to its tier if absent. Return whether changed.

        Order-preserving and idempotent: an entry already present is skipped, so a
        re-run of ``enable`` adds nothing and rewrites nothing.
        """
        return self._rewrite(self._with_entries)

    def deregister(self) -> bool:
        """Remove every owned rule from its tier. Return whether changed.

        Exact value-match removal -- the identity is the entry set itself, so no tag
        schema is needed. Unrelated entries are left untouched.
        """
        return self._rewrite(self._without_entries)

    @staticmethod
    def _with_entries(current: list[str], owned: tuple[str, ...]) -> list[str]:
        """Return *current* with each missing owned rule appended in order."""
        return current + [entry for entry in owned if entry not in current]

    @staticmethod
    def _without_entries(current: list[str], owned: tuple[str, ...]) -> list[str]:
        """Return *current* without any owned rule."""
        return [entry for entry in current if entry not in owned]

    def _rewrite(self, edit: Callable[[list[str], tuple[str, ...]], list[str]]) -> bool:
        """Apply *edit* to each owned tier under the lock. Return whether changed.

        A tier whose edit changes nothing is left as it was, so removing rules
        never creates an absent tier.

        Every lookup after the open goes through the pinned ``.claude/``
        descriptor: the lock, the read, and the atomic replace all land in the
        directory that was checked, never a symlink swapped in afterwards.
        """
        self._refuse_home_repo()
        with (
            PinnedDirectory.opened(self._path.parent) as directory,
            self._lock.held(dir_fd=directory.fd),
        ):
            document = self._load(directory)
            changed = False
            for tier in dict.fromkeys((*self._rules, *_LEGACY_RULES)):
                current = document.rules(tier)
                updated = self._without_entries(
                    edit(current, self._rules.get(tier, ())),
                    _LEGACY_RULES.get(tier, ()),
                )
                if updated != current:
                    document.set_rules(tier, updated)
                    changed = True
            if changed:
                directory.replace_text(self._path.name, document.dumps())
            return changed

    def _refuse_home_repo(self) -> None:
        """Raise ``ValueError`` when the repo is the home directory.

        There ``.claude/settings.json`` is the user-global file. Both ``$HOME``
        and the account's passwd entry are checked, so overriding ``HOME`` does
        not let the real home directory through.
        """
        repo = self._path.parent.parent.resolve()
        homes = {
            Path.home().resolve(),
            Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(),
        }
        if repo in homes:
            msg = (
                f"{self._path}: the repo is your home directory, so this is user-global"
            )
            raise ValueError(msg)

    def _load(self, directory: PinnedDirectory) -> SettingsDocument:
        """Return the settings document; an absent or empty file is ``{}``.

        Invalid UTF-8 raises ``ValueError`` rather than being discarded.
        """
        try:
            text = directory.read_text(self._path.name)
        except UnicodeDecodeError as exc:
            msg = f"{self._path}: settings file is not valid UTF-8"
            raise ValueError(msg) from exc
        return SettingsDocument.parse(text, self._path)
