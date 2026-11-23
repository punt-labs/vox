"""Ordered unresolved tool failures."""

from punt_vox.voxd.hook_context_projection import ToolUpdate
from punt_vox.voxd.hook_failures import HookFailures


def test_stale_failure_cannot_reopen_after_successful_retry() -> None:
    failures = HookFailures()
    failure = ToolUpdate("Bash", "command", "", "failed", True, True)
    success = ToolUpdate("Bash", "command", "", "passed", False, True)
    failures.observe(3, failure)
    assert failures.latest() == failure.as_result(3)
    failures.observe(5, success)
    failures.observe(4, failure)
    assert failures.latest() is None
    assert failures.checkpoint() == [(5, success.checkpoint())]
