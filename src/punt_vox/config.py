"""Read/write for split vox config: ``vox.md`` (durable) + ``vox.local.md`` (ephemeral).

Python components that need config import from here.  Shell hooks read
the same files via their own bash-based reader.  The canonical directory
is ``.punt-labs/vox/`` in the repo root; ``find_config_dir()`` walks up
from cwd to locate it.  All fields return safe defaults when files are
missing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_config_dir
from punt_vox.enablement_state import EnablementState
from punt_vox.frontmatter import Frontmatter
from punt_vox.paths import config_dir as _global_config_dir

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "DEFAULT_CONFIG_DIR",
    "DURABLE_KEYS",
    "EPHEMERAL_KEYS",
    "PERSONAL_KEYS",
    "ConfigStore",
    "SpeakMode",
    "VoxConfig",
    "find_config_dir",
]

# ── Field-to-file routing ────────────────────────────────────────────

# Team-wide durable policy that belongs in tracked ``vox.md``.
DURABLE_KEYS: frozenset[str] = frozenset({"worker_repos"})

# Per-user preferences: writes always land in gitignored ``vox.local.md`` so
# personal toggles never dirty a repository; reads fall back to ``vox.md`` so
# committed team defaults remain honored (tool-enable-disable.md § 2.13).
PERSONAL_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "notify",
        "provider",
        "speak",
        "voice",
    }
)

# Strictly local session state: never read from or written to ``vox.md``, so
# git operations cannot resurrect a stale mood or debug log level.
EPHEMERAL_KEYS: frozenset[str] = frozenset(
    {
        "log_level",
        "vibe",
        "vibe_mode",
        "vibe_nudge_turns",
        "vibe_tags",
    }
)

_LOCAL_WRITE_KEYS: frozenset[str] = PERSONAL_KEYS | EPHEMERAL_KEYS
_DURABLE_READ_KEYS: frozenset[str] = DURABLE_KEYS | PERSONAL_KEYS
ALLOWED_CONFIG_KEYS: frozenset[str] = DURABLE_KEYS | PERSONAL_KEYS | EPHEMERAL_KEYS

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
    log_level: str = "info"  # "info" | "debug" -- the quiet default is info
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

        # Warn before defaulting because auto can enable unwanted nudges.
        vibe_mode = fields.get("vibe_mode", "auto")
        if vibe_mode not in ("auto", "manual", "off"):
            logger.warning(
                "Invalid vibe_mode %r in %s, using 'auto'", vibe_mode, source
            )
            vibe_mode = "auto"

        # An absent or unrecognised log_level is the quiet default; only "debug"
        # raises verbosity, so a garbled value never accidentally silences INFO.
        log_level = fields.get("log_level", "info").lower()
        if log_level not in ("info", "debug"):
            log_level = "info"

        return cls(
            notify=notify,
            speak=speak,
            vibe_mode=vibe_mode,
            voice=fields.get("voice"),
            provider=fields.get("provider"),
            model=fields.get("model"),
            vibe=fields.get("vibe"),
            vibe_tags=fields.get("vibe_tags"),
            log_level=log_level,
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


class SpeakMode:
    """Admit voice/chime toggles and durably record safe rejection metadata."""

    @staticmethod
    def accepts(mode: str) -> bool:
        """Reject unsupported modes without recording caller-controlled text."""
        if mode in ("y", "n"):
            return True
        logger.warning(
            "speak rejected reason=invalid_mode id=%s", uuid.uuid4().hex[:12]
        )
        return False


class ConfigStore:
    """Owns a config directory and provides read/write access to vox config."""

    __slots__ = ("_dir", "_durable", "_durable_only", "_ephemeral", "_repo_name")

    _dir: Path
    _durable: Frontmatter
    _ephemeral: Frontmatter
    _repo_name: str | None
    _durable_only: bool

    def __new__(
        cls, config_dir: Path | None = None, *, durable_only: bool = False
    ) -> Self:
        """Create a config store."""
        self = super().__new__(cls)
        self._dir = config_dir or DEFAULT_CONFIG_DIR
        self._durable = Frontmatter(self._dir / DURABLE_FILENAME)
        self._ephemeral = Frontmatter(self._dir / EPHEMERAL_FILENAME)
        self._repo_name = cls._repo_name_for(self._dir)
        self._durable_only = durable_only
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
        """Read all config fields, layering ``vox.local.md`` over ``vox.md``.

        Team policy (``DURABLE_KEYS``) and team defaults (``PERSONAL_KEYS``)
        load from ``vox.md``. Personal overrides (``PERSONAL_KEYS``) and
        strictly local session state (``EPHEMERAL_KEYS``) overlay from
        ``vox.local.md``. Filtering both sides keeps ``EPHEMERAL_KEYS``
        drift-proof so stale session state in ``vox.md`` is never resurrected.

        When the repository is effectively enabled and neither file sets
        ``notify``, ``notify`` defaults to ``"y"`` without writing durable
        config.
        """
        fields: dict[str, str] = {}

        durable = self._durable.read_fields(strict=self._durable_only)
        fields.update({k: v for k, v in durable.items() if k in _DURABLE_READ_KEYS})

        local = self._ephemeral.read_fields(strict=self._durable_only)
        local_keys = PERSONAL_KEYS if self._durable_only else _LOCAL_WRITE_KEYS
        fields.update({k: v for k, v in local.items() if k in local_keys})

        if (
            not self._durable_only
            and "notify" not in fields
            and self._is_repo_enabled()
        ):
            fields["notify"] = "y"

        return VoxConfig.from_fields(
            fields, repo_name=self._repo_name, source=self._ephemeral.path
        )

    def _is_repo_enabled(self) -> bool:
        """Return whether this config dir belongs to an enabled repository."""
        if self._repo_name is not None:
            return EnablementState.resolve(self._dir.parent.parent).on
        if self._dir == DEFAULT_CONFIG_DIR:
            state = EnablementState.resolve_cwd()
            return state is not None and state.on
        return False

    def read_field(self, field: str, *, strict: bool = False) -> str | None:
        """Read a single config field from the appropriate layer.

        ``EPHEMERAL_KEYS`` read only ``vox.local.md``; ``PERSONAL_KEYS`` read
        ``vox.local.md`` falling back to ``vox.md``; ``DURABLE_KEYS`` read
        ``vox.md``.
        """
        if field in EPHEMERAL_KEYS:
            return self._ephemeral.read_field(field, strict=strict)
        if field in PERSONAL_KEYS:
            local = self._ephemeral.read_field(field, strict=strict)
            if local is not None:
                return local
        return self._durable.read_field(field, strict=strict)

    @classmethod
    def global_dir(cls) -> Path:
        """Return the per-user global config dir (``~/.punt-labs/vox``).

        This is the one location a service-started ``voxd`` -- which never runs
        from a repo -- can read, so ``vox log`` writes ``log_level`` here.
        """
        return _global_config_dir()

    @classmethod
    def resolve_log_level(cls) -> str:
        """Return the effective ``log_level``: repo override, else global, else info.

        ``vox log`` writes the global setting so the daemon (which never runs
        from a repo) reads a value the setter actually wrote; a repo may still
        pin ``log_level`` in its own ``vox.local.md`` to raise only its clients.
        """
        repo = find_config_dir()
        if repo is not None and (level := cls(repo).read_field("log_level")):
            return cls._normalize_level(level)
        return cls._normalize_level(cls(cls.global_dir()).read_field("log_level"))

    @staticmethod
    def _normalize_level(raw: str | None) -> str:
        """Return ``debug`` only when explicitly set, else the quiet ``info``."""
        return "debug" if (raw or "").strip().lower() == "debug" else "info"

    def _layered_check(
        self, check: Callable[[dict[str, str]], None]
    ) -> Callable[[dict[str, str]], None]:
        """Overlay locked local fields onto durable personal defaults for *check*."""

        def _check(held: dict[str, str]) -> None:
            durable = self._durable.read_fields()
            defaults = {k: v for k, v in durable.items() if k in PERSONAL_KEYS}
            check(defaults | held)

        return _check

    def _resolve_check(
        self,
        check: Callable[[dict[str, str]], None] | None,
        store: Frontmatter,
    ) -> Callable[[dict[str, str]], None] | None:
        """Return *check* layered over durable personal defaults for local writes."""
        if check is None or store is not self._ephemeral:
            return check
        return self._layered_check(check)

    def write_field(
        self,
        key: str,
        value: str,
        *,
        # Most field writes have no precondition; check only when supplied.
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write one field, optionally checking that file under its write lock."""
        self._reject_unknown(key)
        store = self._ephemeral if key in _LOCAL_WRITE_KEYS else self._durable
        store.write_fields({key: value}, check=self._resolve_check(check, store))

    def update_field(
        self,
        key: str,
        transform: Callable[[str | None], str],
        *,
        # Most field updates have no precondition; check only when supplied.
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> str:
        """Replace *key* with ``transform(current)`` under the file's lock.

        One locked read-modify-write: a concurrent writer waits rather than
        being overwritten. Returns the stored value.
        """
        self._reject_unknown(key)
        store = self._ephemeral if key in _LOCAL_WRITE_KEYS else self._durable
        return store.update_field(
            key, transform, check=self._resolve_check(check, store)
        )

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Route config fields to their files; a checked write must use one file."""
        for key, value in updates.items():
            self._reject_unknown(key)
            Frontmatter.validate_value(value)

        if check is not None and not (
            updates.keys() <= _LOCAL_WRITE_KEYS or updates.keys() <= DURABLE_KEYS
        ):
            msg = "A checked config write must target one file."
            raise ValueError(msg)

        routes = (
            (self._durable, DURABLE_KEYS),
            (self._ephemeral, _LOCAL_WRITE_KEYS),
        )
        for store, keys in routes:
            subset = {k: v for k, v in updates.items() if k in keys}
            if subset:
                store.write_fields(subset, check=self._resolve_check(check, store))

    @staticmethod
    def _reject_unknown(key: str) -> None:
        """Raise if *key* is not a routable config key."""
        if key not in ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
            msg = f"Unknown config key '{key}'. Allowed: {allowed}"
            raise ValueError(msg)
