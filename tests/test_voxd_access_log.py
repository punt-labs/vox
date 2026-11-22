"""Access logs retain outcomes without retaining caller-controlled targets."""

from __future__ import annotations

import logging

import pytest

from punt_vox.voxd.config import _AccessLogFilter, _install_token_redact_filter


@pytest.mark.parametrize(
    ("target", "label", "status"),
    [
        ("/PRIVATE-PATH", "<unmatched>", 404),
        ("/PRIVATE-PATH?token=PRIVATE-TOKEN", "<unmatched>", 404),
        ("/health", "/health", 200),
        ("/health?token=PRIVATE-TOKEN&other=PRIVATE-QUERY", "/health", 200),
        ("/health?other=PRIVATE-QUERY&token=PRIVATE-TOKEN", "/health", 200),
        ("/health/PRIVATE-PATH", "<unmatched>", 404),
        ("/ws?token=PRIVATE-TOKEN", "/ws", 404),
        ("", "<unmatched>", 404),
        ("/%50RIVATE-PATH", "<unmatched>", 404),
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_access_outcomes(
    target: str,
    label: str,
    status: int,
    method: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("uvicorn.access")
    previous_filters = logger.filters[:]
    previous_level = logger.level
    try:
        _install_token_redact_filter()
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:12345",
            method,
            target,
            "1.1",
            status,
        )
    finally:
        logger.filters = previous_filters
        logger.setLevel(previous_level)
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, f'127.0.0.1:12345 - "{method} {label} HTTP/1.1" {status}')
    ]
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in repr(caplog.records[0].args)


@pytest.mark.parametrize(
    ("message", "args"),
    [
        ("GET /PRIVATE-PATH?token=PRIVATE-TOKEN 404", ()),
        ("unexpected %s", ("/PRIVATE-PATH",)),
        ('%s - "%s %s HTTP/%s" %d', ("/PRIVATE-PATH",)),
        ("unexpected %(target)s", {"target": "/PRIVATE-PATH"}),
    ],
)
def test_unrecognized_records_fail_closed(
    message: str, args: tuple[str, ...] | dict[str, str]
) -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, message, (), None)
    record.args = args
    assert _AccessLogFilter().filter(record)
    assert record.getMessage() == "HTTP access: outcome=unclassified"
    assert record.args == ()
