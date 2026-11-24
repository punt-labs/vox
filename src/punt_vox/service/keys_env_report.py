"""What ``vox daemon install`` did not take from the environment, and why."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import final

# A skipped credential leaves its provider unusable; a setting has a stand-in.
_NO_CREDENTIAL = "nothing stored, so its provider has no credential"
_STAND_INS: dict[str, str] = {
    "TTS_PROVIDER": "vox picks the first ready provider",
    "AWS_DEFAULT_REGION": "boto3 falls back to AWS_REGION or ~/.aws/config",
    "AWS_PROFILE": "boto3 uses the default profile",
}
_UNKEPT = {key: f"not written; {stand_in}" for key, stand_in in _STAND_INS.items()}
_AWS_STAY = "not written; the stored AWS credentials stay"
_REPLACED = "    ~ {}: replaced the stored value"
_REMOVED = "    ~ {}: removed the stored value with the AWS key pair it signed for"


@final
@dataclass(frozen=True, slots=True)
class KeyNotice:
    """One provider key the writer skipped, by name and reason only.

    Never carries the value: a notice is logged and printed to the operator.
    """

    key: str
    reason: str
    kept: bool  # whether a stored value for the key survived the write
    aws_stay: bool = False  # whether the stored AWS credentials it joins stay

    def __str__(self) -> str:
        outcome = "kept the stored value" if self.kept else self._unkept()
        return f"{self.key}: {self.reason}; {outcome}"

    def _unkept(self) -> str:
        """Return what follows from the key having no stored value."""
        return _AWS_STAY if self.aws_stay else _UNKEPT.get(self.key, _NO_CREDENTIAL)


@final
@dataclass(frozen=True, slots=True)
class KeysEnvReport:
    """Where one ``keys.env`` write went, every key it did not take, and rotations.

    *rotated* names the keys whose stored value ``--rotate-keys`` replaced, and
    *removed* the stored AWS keys dropped with the key pair they belonged to.
    """

    path: Path
    notices: tuple[KeyNotice, ...]
    rotated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def notice_lines(self) -> tuple[str, ...]:
        """Return one indented status line per notice and rotation, for install."""
        skipped = (f"    ! {notice}" for notice in self.notices)
        replaced = map(_REPLACED.format, self.rotated)
        return (*skipped, *replaced, *map(_REMOVED.format, self.removed))
