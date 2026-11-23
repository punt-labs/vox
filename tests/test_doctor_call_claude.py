"""Claude login verdicts with the auth command boundary stubbed."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from punt_vox.doctor_call_claude import ClaudeLoginCheck

_CLAUDE_CHECK = "punt_vox.doctor_call_claude"


@pytest.mark.parametrize(
    ("logged_in", "expiry_days", "verdict", "exit_code", "status_kind"),
    [
        (True, 2, "expires soon", 0, "warn"),
        (True, 10, "expires soon", 0, "warn"),
        (True, 10.001, "login: expires ", 0, "pass"),
        (True, 11, "login: expires ", 0, "pass"),
        (True, -1, "expired", 0, "warn"),
        (False, 2, "not signed in", 1, "warn"),
        (False, None, "not signed in", 1, "warn"),
        (True, None, "expiry not reported", 0, "skip"),
    ],
)
def test_claude_login_expiry(
    logged_in: bool,
    expiry_days: float | None,
    verdict: str,
    exit_code: int,
    status_kind: str,
) -> None:
    expiry = (
        (datetime.now(UTC) + timedelta(days=expiry_days)).isoformat()
        if expiry_days is not None
        else None
    )
    proc = subprocess.CompletedProcess(
        [],
        exit_code,
        stdout=json.dumps({"loggedIn": logged_in, "loginExpiresAt": expiry}).encode(),
        stderr=b"secret detail",
    )
    with (
        patch(f"{_CLAUDE_CHECK}.shutil.which", return_value="/bin/claude"),
        patch(f"{_CLAUDE_CHECK}.subprocess.run", return_value=proc) as run,
    ):
        result = ClaudeLoginCheck().run()

    run.assert_called_once_with(
        ["/bin/claude", "auth", "status", "--json"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert verdict in result.message
    assert result.status_kind == status_kind
    assert "secret detail" not in result.message
    assert not result.required


@pytest.mark.parametrize(
    ("payload", "exit_code", "reason"),
    [
        (b"not JSON", 0, "invalid JSON"),
        (b'{"loggedIn":true,"loginExpiresAt":"2026-12-12"}', 0, "no time zone"),
        (b"[]", 0, "expected a JSON object"),
        (b"{}", 0, "sign-in status not reported"),
        (b'{"loggedIn":true}', 2, "exit 2"),
        (b'{"loggedIn":true,"loginExpiresAt":42}', 0, "invalid expiry date"),
    ],
)
def test_claude_login_expiry_does_not_guess(
    payload: bytes, exit_code: int, reason: str
) -> None:
    with (
        patch(f"{_CLAUDE_CHECK}.shutil.which", return_value="/bin/claude"),
        patch(
            f"{_CLAUDE_CHECK}.subprocess.run",
            return_value=subprocess.CompletedProcess([], exit_code, stdout=payload),
        ),
    ):
        result = ClaudeLoginCheck().run()
    assert "expiry could not be checked" in result.message
    assert reason in result.message
    assert result.status_kind == "warn"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (OSError("secret detail"), "OSError"),
        (subprocess.TimeoutExpired("claude", 5), "timed out after 5s"),
    ],
)
def test_claude_login_reports_auth_command_failure(
    failure: Exception, reason: str
) -> None:
    with (
        patch(f"{_CLAUDE_CHECK}.shutil.which", return_value="/bin/claude"),
        patch(f"{_CLAUDE_CHECK}.subprocess.run", side_effect=failure),
    ):
        result = ClaudeLoginCheck().run()
    assert result.status_kind == "warn"
    assert reason in result.message
    assert "secret detail" not in result.message
