"""Additively register vox's repo-scoped permission rules in settings.json.

``tool-enable-disable.md`` § 2.8 lets ``enable`` touch one file outside vox's own
subtree -- ``<repo>/.claude/settings.json`` -- and only additively. The
deterministic entry set (``_VOX_RULES``) is vox's identity in that file:
``Bash(vox:*)`` for a ``--no-plugin`` agent driving the ``vox`` CLI, plus the
plugin's MCP tool wildcard and a ``Skill()`` rule per shipped command, so an
enabled repo never prompts for vox's own tools. Nothing is granted outside the
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

from punt_vox.pinned_directory import PinnedDirectory
from punt_vox.settings_document import SettingsDocument
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["SettingsRegistration"]

# Skill names must match plugin/commands/*.md (namespaced-only commands as
# vox:<name>); scripts/check-skill-permissions.sh enforces the parity.
_VOX_RULES: tuple[str, ...] = (
    "Bash(vox:*)",
    "mcp__plugin_vox_mic__*",
    "Skill(call)",
    "Skill(music)",
    "Skill(mute)",
    "Skill(unmute)",
    "Skill(vibe)",
    "Skill(vox)",
    "Skill(vox:model)",
    "Skill(vox:provider)",
    "Skill(vox:recap)",
    "Skill(vox:voice)",
)


@final
class SettingsRegistration:
    """Register or deregister a deterministic permission-entry set in a settings file.

    Bind the settings path and the entry set at construction. :meth:`register`
    appends the missing entries to ``permissions.allow`` preserving order;
    :meth:`deregister` removes every exact match. Both take the sibling lock for
    the whole read-modify-write, so a parallel ``enable`` never loses an update.
    """

    __slots__ = ("_entries", "_lock", "_path")

    _path: Path
    _lock: SiblingLock
    _entries: tuple[str, ...]

    def __new__(
        cls, settings_path: Path, entries: tuple[str, ...] = _VOX_RULES
    ) -> Self:
        self = super().__new__(cls)
        self._path = settings_path
        self._lock = SiblingLock(settings_path)
        self._entries = entries
        return self

    @property
    def entries(self) -> tuple[str, ...]:
        """Return the deterministic entry set this instance owns."""
        return self._entries

    def register(self) -> bool:
        """Add each entry to ``permissions.allow`` if absent. Return whether changed.

        Order-preserving and idempotent: an entry already present is skipped, so a
        re-run of ``enable`` adds nothing and rewrites nothing.
        """
        return self._rewrite(self._with_entries)

    def deregister(self) -> bool:
        """Remove every owned entry from ``permissions.allow``. Return whether changed.

        Exact value-match removal -- the identity is the entry set itself, so no tag
        schema is needed. Unrelated entries are left untouched.
        """
        return self._rewrite(self._without_entries)

    def _with_entries(self, allow: list[str]) -> list[str]:
        """Return *allow* with each missing owned entry appended in order."""
        return allow + [entry for entry in self._entries if entry not in allow]

    def _without_entries(self, allow: list[str]) -> list[str]:
        """Return *allow* without any owned entry."""
        return [entry for entry in allow if entry not in self._entries]

    def _rewrite(self, edit: Callable[[list[str]], list[str]]) -> bool:
        """Apply *edit* to ``permissions.allow`` under the lock. Return whether changed.

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
            allow = document.allow
            updated = edit(allow)
            if updated == allow:
                return False
            document.set_allow(updated)
            directory.replace_text(self._path.name, document.dumps())
            return True

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
