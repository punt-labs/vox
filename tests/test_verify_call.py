"""The verify-call judge reads a call's report and names each failed expectation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "verify_call.py"
_SPEC = importlib.util.spec_from_file_location("verify_call", _PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
verify_call = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_call"] = verify_call
_SPEC.loader.exec_module(verify_call)

_GOOD = """\
agent: Hi, it's Vox. What are we working on?
user: What does the pallets flask repository say about application factories?
agent: One moment.
tool: read_docs({'repo': 'pallets/flask'}) -> ok
agent: Flask lets you build the app inside a function, which helps testing.
user: Search the web for the weather in Tokyo.
tool: search_web({}) -> ok
agent: It is sunny in Tokyo.
user: Give me a minute, I need to think.
ended by caller
trace: /tmp/call.jsonl
"""


def _verdicts(
    report: str, *, search_key: bool = True, output: str = ""
) -> dict[str, str]:
    judged = verify_call.Judge(report, output, search_key=search_key).verdicts()
    return {v.name: v.status for v in judged}


def test_a_good_call_passes_every_expectation() -> None:
    assert set(_verdicts(_GOOD).values()) == {"PASS"}


def test_the_old_workers_opener_fails_the_greeting() -> None:
    old = _GOOD.replace(
        "Hi, it's Vox. What are we working on?",
        "Hello, this is Vox. I start, steer, check and stop coding workers in vox.",
    )
    assert _verdicts(old)["greeting"] == "FAIL"


def test_a_missing_or_failed_docs_call_fails() -> None:
    assert _verdicts(_GOOD.replace("read_docs", "other"))["docs"] == "FAIL"
    assert (
        _verdicts(
            _GOOD.replace(
                "read_docs({'repo': 'pallets/flask'}) -> ok", "read_docs({}) -> error"
            )
        )["docs"]
        == "FAIL"
    )


def test_markdown_in_an_agent_reply_fails_plain_words() -> None:
    marked = _GOOD.replace("Flask lets you", "## Answer Flask lets you")
    assert _verdicts(marked)["plain words"] == "FAIL"


def test_search_without_a_key_is_a_distinct_skip_never_a_pass() -> None:
    skipped = _verdicts(
        _GOOD.replace("tool: search_web({}) -> ok\n", ""), search_key=False
    )
    assert skipped["search"] == "SKIP"


def test_search_with_a_key_must_call_the_tool() -> None:
    assert (
        _verdicts(_GOOD.replace("tool: search_web({}) -> ok\n", ""))["search"] == "FAIL"
    )


@pytest.mark.parametrize(
    "nag", ["Are you there?", "Are you still there?", "still there"]
)
def test_a_nag_during_silence_fails(nag: str) -> None:
    nagged = _GOOD.replace("ended by caller", f"agent: {nag}\nended by caller")
    assert _verdicts(nagged)["silence"] == "FAIL"


def test_a_runtime_warning_on_hang_up_fails_a_clean_end() -> None:
    warned = _verdicts(_GOOD, output="RuntimeWarning: coroutine x was never awaited")
    assert warned["clean end"] == "FAIL"


def test_a_call_that_never_ended_fails_a_clean_end() -> None:
    assert _verdicts(_GOOD.replace("ended by caller\n", ""))["clean end"] == "FAIL"
