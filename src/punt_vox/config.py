"""Read/write for split vox config: ``vox.md`` (durable) + ``vox.local.md`` (ephemeral).

Python components that need config import from here.  Shell hooks read
the same files via their own bash-based reader.  The canonical directory
is ``.punt-labs/vox/`` in the repo root; ``find_config_dir()`` walks up
from cwd to locate it.  All fields return safe defaults when files are
missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_config_dir
from punt_vox.frontmatter import Frontmatter

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "DEFAULT_CONFIG_DIR",
    "DURABLE_KEYS",
    "EPHEMERAL_KEYS",
    "ConfigStore",
    "VoxConfig",
    "find_config_dir",
]

# ── Field-to-file routing ────────────────────────────────────────────

DURABLE_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "notify",
        "provider",
        "speak",
        "voice",
    }
)

# The whole vibe cluster is session state, not a durable preference. Keeping
# vibe_mode in the tracked vox.md let any git checkout/stash resurrect a stale
# "manual" mode while the gitignored mood lingered. Mode, mood, tags,
# and the nudge cadence counter now live together in the ephemeral vox.local.md.
EPHEMERAL_KEYS: frozenset[str] = frozenset(
    {
        "vibe",
        "vibe_mode",
        "vibe_nudge_turns",
        "vibe_tags",
    }
)

ALLOWED_CONFIG_KEYS: frozenset[str] = DURABLE_KEYS | EPHEMERAL_KEYS

DURABLE_FILENAME = "vox.md"
EPHEMERAL_FILENAME = "vox.local.md"


@dataclass(frozen=True, slots=True)
class VoxConfig:
    """Snapshot of all config fields from vox.md + vox.local.md."""

    notify: str  # "y" | "c" | "n"
    speak: str  # "y" | "n"
    vibe_mode: str  # "auto" | "manual" | "off"
    voice: str | None
    provider: str | None
    model: str | None
    vibe: str | None
    vibe_tags: str | None
    vibe_nudge_turns: int = 0
    repo_name: str | None = None

    @classmethod
    def from_fields(
        cls,
        fields: dict[str, str],
        *,
        repo_name: str | None,
        source: Path,
    ) -> Self:
        """Build a config from a raw field dict, applying validation and defaults.

        *source* names the file a present-but-invalid ``vibe_mode`` came from,
        so the warning points at the offending config rather than a bare value.
        """
        notify = fields.get("notify", "n")
        if notify not in ("y", "c", "n"):
            notify = "n"

        speak = fields.get("speak", "y")
        if speak not in ("y", "n"):
            speak = "y"

        # A present-but-invalid vibe_mode used to fail open to "auto" -- the mode
        # that injects nudges -- silently masking a user's deliberate off/manual.
        # Warn before defaulting; an absent field defaults silently (the legit case).
        vibe_mode = fields.get("vibe_mode", "auto")
        if vibe_mode not in ("auto", "manual", "off"):
            logger.warning(
                "Invalid vibe_mode %r in %s, using 'auto'", vibe_mode, source
            )
            vibe_mode = "auto"

        return cls(
            notify=notify,
            speak=speak,
            vibe_mode=vibe_mode,
            voice=fields.get("voice"),
            provider=fields.get("provider"),
            model=fields.get("model"),
            vibe=fields.get("vibe"),
            vibe_tags=fields.get("vibe_tags"),
            vibe_nudge_turns=cls._parse_int(fields.get("vibe_nudge_turns")),
            repo_name=repo_name,
        )

    @staticmethod
    def _parse_int(raw: str | None) -> int:
        """Return *raw* as a non-negative int, defaulting to 0 on absence or garbage."""
        if raw is None:
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            return 0


class ConfigStore:
    """Owns a config directory and provides read/write access to vox config."""

    __slots__ = ("_dir", "_durable", "_ephemeral", "_repo_name")

    _dir: Path
    _durable: Frontmatter
    _ephemeral: Frontmatter
    _repo_name: str | None

    def __new__(cls, config_dir: Path | None = None) -> Self:
        self = super().__new__(cls)
        self._dir = config_dir or DEFAULT_CONFIG_DIR
        self._durable = Frontmatter(self._dir / DURABLE_FILENAME)
        self._ephemeral = Frontmatter(self._dir / EPHEMERAL_FILENAME)
        self._repo_name = cls._repo_name_for(self._dir)
        return self

    @staticmethod
    def _repo_name_for(config_dir: Path) -> str | None:
        """Return the repo name only when *config_dir* is a repo's ``.punt-labs/vox``.

        A global ``~/.punt-labs/vox`` shares that shape but sits directly under
        ``$HOME``; a tmp dir matches neither.  Deriving a name from a non-repo
        path would prefix spoken phrases with an unrelated directory name, so
        both cases yield ``None``.  The expected shape is read off
        ``DEFAULT_CONFIG_DIR`` to keep the two in lockstep.
        """
        expected = (DEFAULT_CONFIG_DIR.parent.name, DEFAULT_CONFIG_DIR.name)
        repo_root = config_dir.parent.parent
        if (config_dir.parent.name, config_dir.name) != expected:
            return None
        if repo_root == Path.home() or not repo_root.name:
            return None
        return repo_root.name

    @property
    def dir(self) -> Path:
        """Return the config directory."""
        return self._dir

    def read(self) -> VoxConfig:
        """Read all config fields, merging durable and ephemeral files.

        Each file contributes only the keys it owns: durable prefs from
        ``vox.md``, session state from ``vox.local.md``.  Filtering both
        sides keeps the split drift-proof -- a stale ``vibe_mode`` left in a
        committed ``vox.md`` is ignored rather than resurrected.
        """
        fields: dict[str, str] = {}

        durable = self._durable.read_fields()
        fields.update({k: v for k, v in durable.items() if k in DURABLE_KEYS})

        local = self._ephemeral.read_fields()
        fields.update({k: v for k, v in local.items() if k in EPHEMERAL_KEYS})

        return VoxConfig.from_fields(
            fields, repo_name=self._repo_name, source=self._ephemeral.path
        )

    def read_field(self, field: str) -> str | None:
        """Read a single config field from the correct file."""
        if field in EPHEMERAL_KEYS:
            return self._ephemeral.read_field(field)
        return self._durable.read_field(field)

    def write_field(self, key: str, value: str) -> None:
        """Write a single config field to the correct file."""
        self._reject_unknown(key)
        Frontmatter.validate_value(value)

        if key in EPHEMERAL_KEYS:
            self._ephemeral.write_field(key, value)
        else:
            self._durable.write_field(key, value)

    def write_fields(self, updates: dict[str, str]) -> None:
        """Write multiple config fields, routing each to the correct file."""
        for key, value in updates.items():
            self._reject_unknown(key)
            Frontmatter.validate_value(value)

        routes = ((self._durable, DURABLE_KEYS), (self._ephemeral, EPHEMERAL_KEYS))
        for store, keys in routes:
            subset = {k: v for k, v in updates.items() if k in keys}
            if subset:
                store.write_fields(subset)

    @staticmethod
    def _reject_unknown(key: str) -> None:
        """Raise if *key* is not a routable config key."""
        if key not in ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
            msg = f"Unknown config key '{key}'. Allowed: {allowed}"
            raise ValueError(msg)
