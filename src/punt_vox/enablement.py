"""Per-repo enablement: the ``enable`` / ``disable`` / ``purge`` state machine.

Enablement is not a boolean field but a small state machine over three presence
facts (``docs/vox-enable-disable.tex``): the tool-owned directory
``.punt-labs/vox/``, the ``enabled`` marker inside it, and the canonical
``@.punt-labs/vox/CLAUDE.md`` import in the repo ``CLAUDE.md``. The load-bearing
invariant is the § 2.11 biconditional -- the marker is present exactly when the
import is present -- which every transition preserves:

- ``enable``  gitignores local state and locks, deposits the guide, adds the import,
  registers settings, sets an audible notify default, asks the DAEMON for a
  proposed starter provider (design §3.8: the *daemon* chooses, never a local
  probe of the enabling process's environment), writes that provider into
  ``vox.md`` when one was proposed, and finally writes the marker.  Idempotent,
  and the upgrade path.
  An enabled repo is audible by default -- silence is ``disable``.
- ``disable`` removes the import, deletes the marker, deregisters settings, and
  leaves the directory exactly as found (a frame, never a create/remove) -- the
  dormant state.
- ``purge``   is ``disable`` (which removes the import, so no orphan) *then* the
  subtree removal; removing the subtree alone would strand a 404ing import.

:class:`RepoEnablement` is the facade; it composes the import writer
(:class:`~punt_vox.claude_md.ClaudeMdImport`), the
:class:`~punt_vox.deposited_files.VoxMarker`, the
:class:`~punt_vox.deposited_files.DepositedGuide`, the
:class:`~punt_vox.settings_registration.SettingsRegistration`, the
:class:`~punt_vox.audible_notify.AudibleNotify` default, the
:class:`~punt_vox.provider_proposal.ProviderProposal` daemon proposal, and the
:class:`~punt_vox.gitignore_entry.VoxGitignore` for local state and locks.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.audible_notify import AudibleNotify
from punt_vox.claude_md import ClaudeMdImport
from punt_vox.config import ConfigStore
from punt_vox.deposited_files import DepositedGuide, VoxMarker
from punt_vox.dirs import find_repo_root
from punt_vox.gitignore_entry import VoxGitignore
from punt_vox.provider_proposal import EnableOutcome, ProviderProposal
from punt_vox.settings_registration import SettingsRegistration

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["EnableOutcome", "RepoEnablement"]

# The exact canonical repo-scope import line (§ 2.4). Byte-identical across all
# tools and both surfaces; what ``enable`` writes and ``disable`` prunes.
_IMPORT_LINE = "@.punt-labs/vox/CLAUDE.md"


@final
class RepoEnablement:
    """Turn vox on and off in one repo, preserving the marker-import biconditional.

    Bind the seven collaborators at construction; :meth:`for_repo` wires the real
    per-repo paths and :meth:`for_cwd` discovers the repo from the working
    directory. Each transition writes one of the three legal states, so no
    sequence of :meth:`enable` / :meth:`disable` / :meth:`purge` can leave the
    marker and the import disagreeing.
    """

    __slots__ = (
        "_audible",
        "_gitignore",
        "_guide",
        "_import",
        "_marker",
        "_proposal",
        "_settings",
    )

    _import: ClaudeMdImport
    _marker: VoxMarker
    _guide: DepositedGuide
    _settings: SettingsRegistration
    _audible: AudibleNotify
    _proposal: ProviderProposal
    _gitignore: VoxGitignore

    def __new__(
        cls,
        *,
        import_writer: ClaudeMdImport,
        marker: VoxMarker,
        guide: DepositedGuide,
        settings: SettingsRegistration,
        audible: AudibleNotify,
        proposal: ProviderProposal,
        gitignore: VoxGitignore,
    ) -> Self:
        """Create a repo enablement."""
        self = super().__new__(cls)
        self._import = import_writer
        self._marker = marker
        self._guide = guide
        self._settings = settings
        self._audible = audible
        self._proposal = proposal
        self._gitignore = gitignore
        return self

    @classmethod
    def for_repo(cls, repo_root: Path) -> Self:
        """Wire the real per-repo paths for *repo_root*."""
        vox_dir = repo_root / ".punt-labs" / "vox"
        return cls(
            import_writer=ClaudeMdImport(repo_root / "CLAUDE.md", _IMPORT_LINE),
            marker=VoxMarker(vox_dir / "enabled", repo_root),
            guide=DepositedGuide(vox_dir / "CLAUDE.md", repo_root),
            settings=SettingsRegistration(repo_root / ".claude" / "settings.json"),
            audible=AudibleNotify(vox_dir),
            proposal=ProviderProposal(ConfigStore(vox_dir)),
            gitignore=VoxGitignore(repo_root),
        )

    @classmethod
    def for_cwd(cls) -> Self:
        """Wire the repo discovered from the working directory.

        Raises ``ValueError`` when the working directory is not inside a git
        repository -- ``enable`` / ``disable`` are repo-scoped verbs (§ 2.3), so a
        non-repo invocation is a clean boundary failure, not a silent no-op.

        Raises:
            ValueError: If the current directory is not inside a git repository.
        """
        root = find_repo_root()
        if root is None:
            msg = "not inside a git repository"
            raise ValueError(msg)
        return cls.for_repo(root)

    @property
    def root(self) -> Path:
        """Return the repository root this instance operates on."""
        # marker path is <root>/.punt-labs/vox/enabled -> root is three parents up.
        return self._marker.path.parents[2]

    @property
    def marker_path(self) -> Path:
        """Return the ``enabled`` marker path."""
        return self._marker.path

    @property
    def permission_rules(self) -> Mapping[str, tuple[str, ...]]:
        """Return the rules enable adds to ``.claude/settings.json``, by tier."""
        return self._settings.rules

    @property
    def import_line(self) -> str:
        """Return the canonical ``@``-import line enablement owns."""
        return self._import.import_line

    def is_enabled(self) -> bool:
        """Return whether the repo is enabled (the marker is present)."""
        return self._marker.is_present()

    def enable(self) -> EnableOutcome:
        """Reach the Enabled state from anywhere; idempotent (also the upgrade path).

        Order matters for crash-safety: install the ``.gitignore`` rules
        before writing local state or taking the shared host locks.
        Deposit the guide next (so the import never points
        at a missing guide), then the import, the settings, the audible notify
        default, the daemon-proposed provider (§3.8), and the marker **last**.
        The marker is vox's on-signal -- the hooks gate on it -- so if any
        earlier step raises, the repo is left observably OFF (no marker) rather
        than half-on (a marker with no guidance behind it). The audible default
        lands before the marker so a completed ``enable`` is audible: silence
        is ``disable`` (marker gone), never an enabled repo left at ``notify=n``.
        Re-running rewrites the guide, leaves the single import in place,
        preserves an existing audible level, adds no duplicate, and takes the
        ``already_set`` branch on the provider slot (§3.8: enable never
        overrules a human's declared provider, so a re-run does not thrash it).

        Returns the outcome of the provider-proposal step (design §3.8):
        ``written`` / ``already_set`` are success paths; ``voxd_unavailable``
        / ``no_ready_provider`` are true reports of an unusable host, not
        errors -- the rest of enable still landed and the caller sees which
        slot did not get filled.

        Raises:
            OSError: If an enablement artifact cannot be read, deposited, or registered.
            ValueError: If an existing repo artifact is malformed or unsafe to modify.
        """
        self._gitignore.ensure()
        self._guide.deposit()
        self._import.register()
        self._settings.register()
        self._audible.ensure_audible()
        outcome = self._proposal.propose_and_write()
        self._marker.write()
        return outcome

    def disable(self) -> None:
        """Reach the Dormant/Absent state non-destructively.

        Remove the import first (so the biconditional holds the moment the marker
        goes), delete the marker, and deregister the settings entries. The
        directory is left exactly as found -- ``disable`` never creates or removes
        it -- so it lands in Dormant when a directory was present and stays Absent
        when it was not.

        Raises:
            OSError: If the import, marker, or permission entries cannot be removed.
            ValueError: If an existing repo artifact is malformed or unsafe to modify.
        """
        self._import.prune()
        self._marker.remove()
        self._settings.deregister()

    def purge(self) -> None:
        """Reach the Absent state by removing the subtree, leaving no orphan import.

        ``purge`` is ``disable`` -- which removes the import line that lives in
        ``CLAUDE.md``, *outside* the subtree -- followed by the subtree removal.
        Removing the subtree alone would strand a 404ing ``@``-import and violate
        the § 2.11 biconditional.

        Raises:
            OSError: If repo artifacts cannot be read, updated, or removed.
            ValueError: If an existing repo artifact is malformed or unsafe to modify.
        """
        self.disable()
        self._remove_subtree()

    def _remove_subtree(self) -> None:
        """Remove the ``.punt-labs/vox/`` directory if it is present."""
        vox_dir = self._marker.path.parent
        if vox_dir.is_dir():
            shutil.rmtree(vox_dir)
