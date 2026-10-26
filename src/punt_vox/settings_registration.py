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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.sibling_lock import SiblingLock

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

    __slots__ = ("_entries", "_file", "_lock")

    _file: AtomicFile
    _lock: SiblingLock
    _entries: tuple[str, ...]

    def __new__(
        cls, settings_path: Path, entries: tuple[str, ...] = _VOX_RULES
    ) -> Self:
        self = super().__new__(cls)
        self._file = AtomicFile(settings_path)
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
        self._refuse_unsafe_path()
        with self._lock.held():
            data = self._load()
            allow = self._existing_allow(data)
            missing = [entry for entry in self._entries if entry not in allow]
            if not missing:
                return False
            self._store_allow(data, allow + missing)
            return True

    def deregister(self) -> bool:
        """Remove every owned entry from ``permissions.allow``. Return whether changed.

        Exact value-match removal -- the identity is the entry set itself, so no tag
        schema is needed. Unrelated entries are left untouched.
        """
        self._refuse_unsafe_path()
        with self._lock.held():
            data = self._load()
            allow = self._existing_allow(data)
            kept = [entry for entry in allow if entry not in self._entries]
            if len(kept) == len(allow):
                return False
            self._store_allow(data, kept)
            return True

    def _refuse_unsafe_path(self) -> None:
        """Raise ``ValueError`` unless the write would land in the repo's own file.

        A symlinked ``.claude/`` or ``settings.json`` could point anywhere, and
        a repo rooted at ``$HOME`` makes this the user-global settings file.
        """
        path = self._file.path
        if path.parent.is_symlink() or path.is_symlink():
            msg = f"{path}: refusing a symlinked .claude/settings.json"
            raise ValueError(msg)
        if path.parent.parent.resolve() == Path.home().resolve():
            msg = f"{path}: the repo is your home directory, so this is user-global"
            raise ValueError(msg)

    def _load(self) -> dict[str, Any]:
        """Return the parsed settings object, or ``{}`` when the file is empty/absent.

        A malformed existing file is a boundary error (PY-EH-1/PY-EH-8): raise
        rather than silently discard a user's settings, which resetting to ``{}``
        would do. ``dict[str, Any]`` is the JSON wire boundary -- the shape is the
        user's whole settings file, narrowed only where this class writes.
        """
        try:
            text = self._file.read()
        except UnicodeDecodeError as exc:
            msg = f"{self._file.path}: settings file is not valid UTF-8"
            raise ValueError(msg) from exc
        if not text.strip():
            return {}
        # The wire boundary: JSON deserialization yields object until narrowed.
        parsed: object = json.loads(text)
        if not isinstance(parsed, dict):
            msg = f"{self._file.path}: settings root must be a JSON object"
            raise ValueError(msg)
        return cast("dict[str, Any]", parsed)

    def _store_allow(self, data: dict[str, Any], allow: list[str]) -> None:
        """Write *allow* back into ``data.permissions.allow`` and replace the file.

        Only an *absent* ``permissions`` key is created here; a present one is
        guaranteed a dict by :meth:`_existing_allow` (which runs first and raises
        on a malformed value -- including an explicit ``null``), so this never
        overwrites the user's data.
        """
        if "permissions" not in data:
            data["permissions"] = {}
        perms = cast("dict[str, Any]", data["permissions"])
        perms["allow"] = allow
        self._file.replace(json.dumps(data, indent=2) + "\n")

    def _existing_allow(self, data: dict[str, Any]) -> list[str]:
        """Return the current ``permissions.allow`` entries as strings, else ``[]``.

        Symmetric with :meth:`_load`'s root check: a *present but malformed*
        nested value is a boundary error, not an empty default. Presence is keyed
        on membership, never on truthiness -- a present but ``null`` ``permissions``
        (or ``permissions.allow``) is malformed and raises, exactly like a string
        or number would, rather than being read as absent and silently overwritten.
        Only a genuinely *absent* key is safe to treat as empty and create later.
        """
        if "permissions" not in data:
            return []
        perms: Any = data["permissions"]
        if not isinstance(perms, dict):
            msg = f"{self._file.path}: permissions must be a JSON object"
            raise ValueError(msg)
        perms_dict = cast("dict[str, Any]", perms)
        if "allow" not in perms_dict:
            return []
        allow: Any = perms_dict["allow"]
        if not isinstance(allow, list):
            msg = f"{self._file.path}: permissions.allow must be a JSON array"
            raise ValueError(msg)
        return [str(entry) for entry in cast("list[object]", allow)]
