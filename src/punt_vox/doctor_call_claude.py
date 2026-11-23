"""Read the current Claude Code sign-in status without touching credentials."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Self, cast, final

from punt_vox.doctor_result import OK, OPTIONAL, WARN, CheckResult

_LOGIN_WARNING = timedelta(days=10)
_AUTH_TIMEOUT_S = 5


@final
class ClaudeLoginCheck:
    """Report the end of the stored sign-in, not the OAuth refresh interval."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a Claude login check."""
        return super().__new__(cls)

    def run(self) -> CheckResult:
        """Ask Claude Code for auth status in the invoking shell's environment."""
        claude = shutil.which("claude")
        if claude is None:
            return CheckResult.of(
                OPTIONAL, "Claude login: Claude Code not found", required=False
            )
        try:
            result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
                [claude, "auth", "status", "--json"],
                capture_output=True,
                timeout=_AUTH_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._unknown(f"timed out after {_AUTH_TIMEOUT_S}s")
        except OSError as exc:
            return self._unknown(type(exc).__name__)
        try:
            payload: object = json.loads(result.stdout)
        except (ValueError, RecursionError):
            reason = (
                f"exit {result.returncode}" if result.returncode else "invalid JSON"
            )
            return self._unknown(reason)
        if not isinstance(payload, dict):
            return self._unknown("expected a JSON object")
        status = cast("dict[str, object]", payload)
        if result.returncode and status.get("loggedIn") is not False:
            return self._unknown(f"exit {result.returncode}")
        return self._status(status, datetime.now(UTC))

    @classmethod
    def _status(cls, payload: dict[str, object], now: datetime) -> CheckResult:
        value = payload.get("loginExpiresAt")
        if payload.get("loggedIn") is False:
            return cls._warning("not signed in — run 'claude auth login'")
        if payload.get("loggedIn") is not True:
            return cls._unknown("sign-in status not reported")
        if isinstance(value, str):
            try:
                expires = datetime.fromisoformat(value)
            except ValueError:
                return cls._unknown("invalid expiry date")
            if expires.utcoffset() is None:
                return cls._unknown("expiry date has no time zone")
            return cls._expiry(expires, now)
        if value is None:
            return CheckResult.of(
                OPTIONAL, "Claude login: active; expiry not reported", required=False
            )
        return cls._unknown("invalid expiry date")

    @classmethod
    def _expiry(cls, expires: datetime, now: datetime) -> CheckResult:
        date = expires.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if expires <= now:
            return cls._warning(f"expired ({date}) — run 'claude auth login'")
        if expires - now <= _LOGIN_WARNING:
            return cls._warning(f"expires soon ({date}) — run 'claude auth login'")
        return CheckResult.of(OK, f"Claude login: expires {date}", required=False)

    @classmethod
    def _unknown(cls, reason: str) -> CheckResult:
        return cls._warning(
            f"expiry could not be checked ({reason}) — run 'claude auth status'"
        )

    @staticmethod
    def _warning(message: str) -> CheckResult:
        return CheckResult.of(WARN, f"Claude login: {message}", required=False)
