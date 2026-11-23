"""ElevenLabs scope verdicts with the provider boundary stubbed."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from punt_vox.doctor_call_scope import ConvAIKeyCheck

if TYPE_CHECKING:
    from pathlib import Path

_SCOPE_CHECK = "punt_vox.doctor_call_scope"


@pytest.mark.parametrize(
    ("http_status", "detail", "verdict"),
    [
        (
            404,
            {
                "status": "document_not_found",
                "message": "Document with id __vox_doctor_scope_probe__ not found.",
            },
            "granted",
        ),
        (404, {"code": "agent_not_found"}, "granted"),
        (
            401,
            {
                "status": "missing_permissions",
                "message": "The API key is missing the permission convai_write",
            },
            "missing scope",
        ),
        (
            403,
            {
                "status": "missing_permissions",
                "message": "The API key is missing the permission convai_write",
            },
            "missing scope",
        ),
        (401, {"status": "invalid_api_key"}, "invalid key"),
        (403, {"status": "invalid_api_key"}, "invalid key"),
        (403, {"status": "insufficient_permissions"}, "could not verify scope"),
        (404, {}, "could not verify scope"),
        (500, "unexpected provider reply", "could not verify scope"),
    ],
)
def test_elevenlabs_scope_probe(
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    detail: object,
    verdict: str,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    response = httpx.Response(http_status, json={"detail": detail})
    with patch(f"{_SCOPE_CHECK}.httpx.get", return_value=response) as request:
        result = ConvAIKeyCheck().run()

    request.assert_called_once_with(
        "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
        headers={"xi-api-key": "test-key"},
        params={"agent_id": "__vox_doctor_scope_probe__"},
        timeout=5.0,
    )
    assert verdict in result.message
    assert "test-key" not in result.message
    assert result.status_kind == ("pass" if verdict == "granted" else "warn")
    assert not result.required


def test_elevenlabs_connection_failure_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    with patch(
        f"{_SCOPE_CHECK}.httpx.get", side_effect=httpx.ConnectTimeout("secret detail")
    ):
        result = ConvAIKeyCheck().run()
    assert "could not verify scope (ConnectTimeout)" in result.message
    assert "secret detail" not in result.message


@pytest.mark.parametrize("body", [b"<html>not found</html>", b"[]"])
def test_elevenlabs_unstructured_reply_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    response = httpx.Response(404, content=body)
    with patch(f"{_SCOPE_CHECK}.httpx.get", return_value=response):
        result = ConvAIKeyCheck().run()
    assert "could not verify scope (HTTP 404)" in result.message
    assert result.status_kind == "warn"


def test_elevenlabs_missing_tls_certificate_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    result = ConvAIKeyCheck().run()
    assert "could not verify scope (FileNotFoundError)" in result.message
    assert result.status_kind == "warn"
    assert not result.required


def test_elevenlabs_non_ascii_key_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key”")
    result = ConvAIKeyCheck().run()
    assert "invalid key: contains non-ASCII" in result.message
    assert "test-key" not in result.message
    assert result.status_kind == "warn"


def test_elevenlabs_probe_uses_the_configured_key_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = " test-key "
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    with patch(
        f"{_SCOPE_CHECK}.httpx.get",
        side_effect=httpx.LocalProtocolError("secret detail"),
    ) as request:
        result = ConvAIKeyCheck().run()
    assert request.call_args.kwargs["headers"] == {"xi-api-key": key}
    assert result.status_kind == "warn"
    assert key not in result.message
