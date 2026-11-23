from __future__ import annotations

import io
import json

import pytest

from punt_vox.voxd.voice_agent import EventTrace


def test_trace_redacts_nested_credentials() -> None:
    output = io.StringIO()
    EventTrace(output).record(
        "note",
        "test",
        {
            "nested": [
                {
                    "mixedToken": "raw",
                    "client_SECRET": "raw",
                    "apiKey": "raw",
                    "signedUrl": "raw",
                    "normal": "visible",
                }
            ],
            "signed_url": "raw",
            "status": "ok",
        },
    )
    assert json.loads(output.getvalue())["detail"] == {
        "nested": [
            {
                "mixedToken": "[redacted]",
                "client_SECRET": "[redacted]",
                "apiKey": "[redacted]",
                "signedUrl": "[redacted]",
                "normal": "visible",
            }
        ],
        "signed_url": "[redacted]",
        "status": "ok",
    }
    assert "raw" not in output.getvalue()


@pytest.mark.parametrize("event", ["client_tool_call", "worker_steer_receipt"])
@pytest.mark.parametrize(
    "parameters,expected",
    [
        ({}, {}),
        (
            {"choice": "approve", "prompt_seq": 4},
            {"choice": "approve", "prompt_seq": 4},
        ),
        ({"choice": "deny", "prompt_seq": 9}, {"choice": "deny", "prompt_seq": 9}),
        (
            {"choice": "private spoken answer", "prompt_seq": "private spoken answer"},
            {"choice": None, "prompt_seq": None},
        ),
        (
            {
                "choice": ["private spoken answer"],
                "prompt_seq": {"words": "private spoken answer"},
            },
            {"choice": None, "prompt_seq": None},
        ),
        ({"choice": None, "prompt_seq": True}, {"choice": None, "prompt_seq": None}),
        ({"prompt_seq": 0}, {"prompt_seq": None}),
        ({"prompt_seq": -1}, {"prompt_seq": None}),
    ],
)
def test_trace_retains_only_semantic_permission_fields(
    event: str, parameters: dict[str, object], expected: dict[str, object]
) -> None:
    output = io.StringIO()
    EventTrace(output).record("note", event, parameters)
    assert json.loads(output.getvalue())["detail"] == expected
    assert "private spoken answer" not in output.getvalue()
