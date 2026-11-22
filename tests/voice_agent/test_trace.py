from __future__ import annotations

import io
import json

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
