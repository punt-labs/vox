"""Probe the local ElevenLabs key without provisioning or dialing an agent."""

from __future__ import annotations

import os
from typing import Self, cast, final

import httpx

from punt_vox.doctor_result import OK, OPTIONAL, WARN, CheckResult

_SCOPE = "convai_write"
_PROBE_AGENT = "__vox_doctor_scope_probe__"
_PROBE_URL = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
_LABEL = f"ElevenLabs {_SCOPE} (local ELEVENLABS_API_KEY)"
_REQUEST_TIMEOUT_S = 5.0


@final
class ConvAIKeyCheck:
    """Distinguish a missing scope from an inconclusive API response."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create an ElevenLabs Agents scope check."""
        return super().__new__(cls)

    def run(self) -> CheckResult:
        """Check the shell key; voxd may have a separate service environment."""
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            return CheckResult.of(OPTIONAL, f"{_LABEL}: no key set", required=False)
        try:
            # The signed URL endpoint requires convai_write. No socket is
            # opened, so even an unexpected success cannot start a call.
            response = httpx.get(
                _PROBE_URL,
                headers={"xi-api-key": key},
                params={"agent_id": _PROBE_AGENT},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except UnicodeError:
            return self._warning("invalid key: contains non-ASCII characters")
        except (httpx.HTTPError, OSError) as exc:
            return self._warning(f"could not verify scope ({type(exc).__name__})")
        return self._status(response)

    @classmethod
    def _status(cls, response: httpx.Response) -> CheckResult:
        detail = cls._error_detail(response)
        status = detail.get("status")
        message = detail.get("message")
        if response.status_code == 404 and cls._missing_agent(detail):
            return CheckResult.of(OK, f"{_LABEL}: granted", required=False)
        if response.status_code in (401, 403):
            if (
                status == "missing_permissions"
                and isinstance(message, str)
                and _SCOPE in message
            ):
                return cls._warning(
                    "missing scope — enable ElevenAgents Write for the key"
                )
            if status == "invalid_api_key":
                return cls._warning("invalid key")
        return cls._warning(f"could not verify scope (HTTP {response.status_code})")

    @staticmethod
    def _missing_agent(detail: dict[str, object]) -> bool:
        if detail.get("code") == "agent_not_found":
            return True
        message = detail.get("message")
        return (
            detail.get("status") == "document_not_found"
            and isinstance(message, str)
            and _PROBE_AGENT in message
        )

    @staticmethod
    def _warning(message: str) -> CheckResult:
        return CheckResult.of(WARN, f"{_LABEL}: {message}", required=False)

    @staticmethod
    def _error_detail(response: httpx.Response) -> dict[str, object]:
        try:
            payload: object = response.json()
        except (ValueError, RecursionError):
            return {}
        if not isinstance(payload, dict):
            return {}
        detail = cast("dict[str, object]", payload).get("detail")
        return cast("dict[str, object]", detail) if isinstance(detail, dict) else {}
