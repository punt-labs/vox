"""The append-only audit log of baseline verdicts and relaxations."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Self


class AuditLog:
    """Append and query ``.oo-audit.jsonl`` — the ratchet's decision trail."""

    _path: Path

    FILENAME: str = ".oo-audit.jsonl"

    def __new__(cls, root: Path) -> Self:
        self = super().__new__(cls)
        self._path = root / cls.FILENAME
        return self

    @property
    def path(self) -> Path:
        """Return the on-disk audit log path."""
        return self._path

    @property
    def exists(self) -> bool:
        """Return whether the audit log is present on disk."""
        return self._path.exists()

    def append(
        self,
        *,
        files_scored: int,
        files_improved: int,
        files_regressed: int,
        verdict: str,
        deltas: dict[str, dict[str, list[float]]],
        source: str | None,
        commit: str | None,
        reason: str | None = None,
    ) -> None:
        """Append one verdict entry, recording its source (PR/bead ref).

        ``reason`` carries the human justification for a ``relaxed`` verdict;
        it is an audit marker, not an enforcement gate.
        """
        entry = {
            "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": commit,
            "source": source,
            "files_scored": files_scored,
            "files_improved": files_improved,
            "files_regressed": files_regressed,
            "verdict": verdict,
            "reason": reason,
            "deltas": deltas,
        }
        with self._path.open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def relaxations_since(self, base_text: str | None) -> frozenset[tuple[str, str]]:
        """Return (file, metric) pairs relaxed by the *current* change only.

        A relaxation counts only when its audit line is absent from the base
        commit's audit log (``base_text``). This scopes the waiver to the
        change under review, so a historical relaxation cannot bless a fresh
        regression re-locked via ``--rebaseline`` (S7 / gvr).
        """
        base_lines = set(base_text.splitlines()) if base_text else set()
        pairs: set[tuple[str, str]] = set()
        for line in self._raw_lines():
            if line in base_lines:
                continue
            entry: dict[str, object] = json.loads(line)
            if entry.get("verdict") != "relaxed":
                continue
            deltas = entry.get("deltas")
            if not isinstance(deltas, dict):
                continue
            for path, metrics in deltas.items():
                if isinstance(metrics, dict):
                    pairs.update((path, metric) for metric in metrics)
        return frozenset(pairs)

    def _raw_lines(self) -> list[str]:
        if not self._path.exists():
            return []
        return [ln for ln in self._path.read_text().splitlines() if ln.strip()]

    def _read(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self._raw_lines()]

    def render_log(self) -> list[str]:
        """Return the audit history as report lines."""
        if not self._path.exists():
            return ["No audit log found"]
        lines = [
            f"\n{'Timestamp':<22} {'Commit':<10} {'Scored':>7} "
            f"{'Improved':>9} {'Regressed':>10} {'Verdict':>8}",
            "-" * 70,
        ]
        for entry in self._read():
            commit = entry.get("commit") or "?"
            lines.append(
                f"{entry.get('ts', '?')!s:<22} {commit!s:<10} "
                f"{entry.get('files_scored', 0)!s:>7} "
                f"{entry.get('files_improved', 0)!s:>9} "
                f"{entry.get('files_regressed', 0)!s:>10} "
                f"{entry.get('verdict', '?')!s:>8}"
            )
        return lines
