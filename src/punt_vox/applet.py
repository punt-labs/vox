"""Lux display applet: builds element tree, connects to display server."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from punt_vox.config import VoxConfig

logger = logging.getLogger(__name__)

# Mood presets offered in the vibe combo.
_MOOD_PRESETS: list[str] = [
    "",
    "chill",
    "excited",
    "focused",
    "3am debugging",
    "celebrating",
    "tired",
]


# Hook events and their activation rules.
# Each tuple: (display_label, script_name, activation_rule)
# activation_rule is a string interpreted by _is_hook_active.
_HOOK_EVENTS: list[tuple[str, str, str]] = [
    ("SessionStart", "session-start.sh", "always"),
    ("Stop", "notify.sh", "notify"),
    ("Post-Bash", "signal.sh", "always"),
    ("Notification", "notify-permission.sh", "notify"),
    ("UserPromptSubmit", "acknowledge.sh", "continuous"),
    ("SubagentStart", "subagent.sh", "continuous"),
    ("SubagentStop", "subagent.sh", "continuous"),
    ("PreCompact", "pre-compact.sh", "continuous"),
    ("SessionEnd", "farewell.sh", "notify"),
]


def _is_hook_active(cfg: VoxConfig, rule: str) -> bool:
    """Determine if a hook is active given config state and its activation rule."""
    if rule == "always":
        return True
    if rule == "notify":
        return cfg.notify in ("y", "c")
    if rule == "continuous":
        return cfg.notify == "c"
    return False


def _build_info_tooltip(cfg: VoxConfig, provider_name: str) -> str:
    """Build tooltip with engine info and active hooks."""
    active_hooks = [
        label for label, _script, rule in _HOOK_EVENTS if _is_hook_active(cfg, rule)
    ]
    lines: list[str] = [f"Engine: {provider_name}"]
    if active_hooks:
        lines.append("")
        lines.append("Active hooks")
        lines.extend(f"  {label}" for label in active_hooks)
    return "\n".join(lines)


def build_vox_elements(
    cfg: VoxConfig,
    provider_name: str,
    voice_roster: list[str],
) -> list[Any]:
    """Pure function — builds Lux element instances from Vox state.

    Returns a list of element dataclass instances from punt_lux.protocol.
    Requires punt-lux to be installed.
    """
    from punt_lux.protocol import (  # pyright: ignore[reportMissingImports]
        ComboElement,
        RadioElement,
        TextElement,
    )

    # --- Notifications label + info icon (tooltip on the text) ---
    info_tooltip = _build_info_tooltip(cfg, provider_name)
    notify_label = TextElement(
        id="vox-notify-label",
        content="Notifications  \u24d8",
        tooltip=info_tooltip,
    )

    # --- Notifications: Off | On | Continuous ---
    notify_map = {"n": 0, "y": 1, "c": 2}
    notify_selected = notify_map.get(cfg.notify, 0)
    notify_radio = RadioElement(
        id="vox-notify",
        label="",
        items=["Off", "On", "Continuous"],
        selected=notify_selected,
    )

    # --- Voice: Mute | Unmute ---
    speak_map = {"n": 0, "y": 1}
    speak_selected = speak_map.get(cfg.speak, 1)
    speak_radio = RadioElement(
        id="vox-speak",
        label="Voice",
        items=["Mute", "Unmute"],
        selected=speak_selected,
    )

    # --- At the Mic: voice combo ---
    current_voice = cfg.voice or ""
    try:
        voice_index = voice_roster.index(current_voice)
    except ValueError:
        voice_index = 0
    voice_combo = ComboElement(
        id="vox-voice",
        label="At the Mic",
        items=voice_roster if voice_roster else ["(none)"],
        selected=voice_index,
    )

    # --- Vibe: mood combo ---
    current_vibe = cfg.vibe or ""
    vibe_items = list(_MOOD_PRESETS)
    if current_vibe and current_vibe not in vibe_items:
        vibe_items.append(current_vibe)
    try:
        vibe_index = vibe_items.index(current_vibe)
    except ValueError:
        vibe_index = 0
    vibe_combo = ComboElement(
        id="vox-vibe",
        label="Vibe",
        items=vibe_items,
        selected=vibe_index,
    )

    return [notify_label, notify_radio, speak_radio, voice_combo, vibe_combo]


def show_applet(
    cfg: VoxConfig,
    provider_name: str,
    voice_roster: list[str],
) -> dict[str, Any]:
    """Connect to Lux via LuxClient, send scene. Returns status dict."""
    try:
        from punt_lux.client import LuxClient  # pyright: ignore[reportMissingImports]
    except ImportError:
        return {
            "status": "error",
            "message": "punt-lux is not installed. Install with: uv add punt-vox[lux]",
        }

    elements = build_vox_elements(cfg, provider_name, voice_roster)

    try:
        with LuxClient(name="vox-applet") as client:
            show_kwargs: dict[str, Any] = {
                "frame_id": "vox",
                "frame_title": "Vox",
            }
            # frame_size and frame_flags require punt-lux >=0.10
            sig = inspect.signature(client.show)  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
            if "frame_size" in sig.parameters:
                show_kwargs["frame_size"] = (340, 120)
                show_kwargs["frame_flags"] = {
                    "auto_resize": True,
                    "no_collapse": True,
                }
            client.show("vox-status", elements, **show_kwargs)
    except OSError as exc:
        return {"status": "error", "message": f"Lux display not available: {exc}"}
    except RuntimeError as exc:
        return {"status": "error", "message": f"Lux display error: {exc}"}

    return {"status": "ok"}
