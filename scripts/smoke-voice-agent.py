"""Run one billed ElevenLabs text-only turn and print the redacted trace."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from punt_vox.voxd.voice_agent import (
    ClientTool,
    ConvAIControl,
    ConvAIError,
    Conversation,
    EventTrace,
    SessionOptions,
    VoiceAgent,
)
from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentToolResponse,
    ClientToolCall,
)

_CLOCK = ClientTool(
    "clock",
    "Read the current UTC date and time.",
    {"type": "object", "properties": {}, "required": []},
)
_PROMPT = (
    "Call the clock tool once, then include its UTC timestamp verbatim in your reply."
)


async def clock_turn(
    signed_url: str, tool_ids: tuple[str, ...], trace: EventTrace
) -> None:
    options = SessionOptions(_PROMPT, tool_ids, text_only=True)
    async with (
        asyncio.timeout(30),
        Conversation.open(signed_url, options, trace=trace) as call,
    ):
        await call.send_text("Call clock and tell me the current UTC time.")
        started: tuple[str, float, str] | None = None
        acknowledged = False
        replied = False
        while not (acknowledged and replied):
            match await call.receive():
                case ClientToolCall(tool_name="clock", expects_response=True) as event:
                    if started is not None:
                        msg = "The agent called clock more than once"
                        raise ConvAIError(msg)
                    utc = datetime.now(UTC).isoformat(timespec="seconds")
                    started = (event.tool_call_id, monotonic(), utc)
                    await call.send_tool_result(event.tool_call_id, utc)
                case AgentToolResponse() as ack if (
                    started and ack.tool_call_id == started[0] and not acknowledged
                ):
                    if ack.is_error:
                        msg = "ElevenLabs rejected the clock result"
                        raise ConvAIError(msg)
                    trace.record(
                        "note",
                        "clock_round_trip",
                        {
                            "tool_call_id": ack.tool_call_id,
                            "round_trip_ms": round(
                                (monotonic() - started[1]) * 1000, 2
                            ),
                        },
                    )
                    acknowledged = True
                case AgentResponse(text=text) if started and started[2] in text:
                    trace.record("note", "clock_reply_verified", {"utc": started[2]})
                    replied = True
                case _:
                    continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "state_dir", type=Path, help="dedicated smoke-test config directory"
    )
    parser.add_argument(
        "--teardown-only", action="store_true", help="retry cleanup without a call"
    )
    args = parser.parse_args()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        parser.error(
            "set ELEVENLABS_API_KEY to a key with ElevenLabs Agents write access"
        )
    with ConvAIControl(key) as control:
        agent = VoiceAgent(control, path=args.state_dir / "voice-agent.json")
        if args.teardown_only:
            agent.teardown()
            return
        if (args.state_dir / "voice-agent.json").exists():
            parser.error("use a fresh state directory or run --teardown-only first")
        try:
            signed_url, tool_ids = agent.connection((_CLOCK,))
            asyncio.run(clock_turn(signed_url, tool_ids, EventTrace(sys.stdout)))
        finally:
            agent.teardown()


if __name__ == "__main__":
    main()
