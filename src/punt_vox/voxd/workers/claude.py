"""Claude Code is one harness profile, with inherited user permissions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self, final

from punt_vox.process_env import ProcessEnv
from punt_vox.voxd.hook_context_projection import HookText
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.trust import ClaudeTrust


@final
class ClaudeProfile:
    """Launch Claude with a session identity shared with the hook store."""

    __slots__ = ("_store", "_trust")
    _store: HookContextStore
    _trust: ClaudeTrust
    submit_key = "Enter"
    interrupt_key = "Escape"
    settle_delay = 0.5
    input_limit = HookText.LIMIT
    ENV_NAMES = frozenset(
        {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    )

    def __new__(cls, store: HookContextStore) -> Self:
        """Bind the daemon's shared live context store."""
        self = super().__new__(cls)
        self._store = store
        self._trust = ClaudeTrust()
        return self

    @staticmethod
    def argv(worker_id: str, task: str) -> list[str]:
        """Use interactive Claude, never a separate permission profile."""
        return ["claude", "--session-id", worker_id, "--", task]

    def environment(self, parent: Mapping[str, str]) -> dict[str, str]:
        """Admit base process variables and explicitly named Claude credentials."""
        environment = {
            k: v
            for k, v in parent.items()
            if ProcessEnv.admits(k) or k in self.ENV_NAMES
        }
        if directory := environment.get("CLAUDE_CONFIG_DIR"):
            environment["CLAUDE_CONFIG_DIR"] = str(Path(directory).resolve())
        return environment

    def prepare(self, worktree: Path) -> None:
        """Accept trust only for this exact absolute worktree."""
        self._trust.update(worktree, accepted=True)

    # PY-TS-14: no receipt exists before the first UserPromptSubmit observation.
    def last_input(self, worker_id: str) -> WorkerInput | None:
        """Use UserPromptSubmit only; Escape need not produce a Stop hook."""
        seq, text = self._store.last_input(worker_id)
        return WorkerInput(seq, text) if seq else None

    @staticmethod
    def matches_input(receipt: WorkerInput, words: str) -> bool:
        """Match an untruncated input receipt exactly."""
        return receipt.text == words

    def inputs_since(self, worker_id: str, baseline: int) -> list[WorkerInput]:
        """Search retained submissions, not just the latest prompt at poll time."""
        return [
            WorkerInput(seq, text)
            for seq, text in self._store.inputs_since(worker_id, baseline)
        ]

    def cleanup(self, worktree: Path) -> None:
        """Remove the temporary trust flag, retaining work product."""
        self._trust.update(worktree, accepted=False)

    # PY-TS-14: curated hook JSON is a snapshot; fields evolve with the hook source.
    def progress(self, worker_id: str) -> dict[str, object]:
        """SessionStart, not pane existence, establishes readiness."""
        try:
            return dict(self._store.snapshot(worker_id))
        except ValueError:
            return {"session_started": False}
