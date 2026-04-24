#!/usr/bin/env python3
"""
Voice Mode Tool

Controls per-chat voice mode: text_only, voice_only, or all.
Reads and writes ~/.hermes/gateway_voice_mode.json so the gateway
platform adapter (base.py) can read it on every response delivery.

Usage:
    from tools.voice_mode_tool import voice_mode_tool
    result = voice_mode_tool(mode="voice_only", chat_id="5908295481")
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

HERMES_HOME = Path.home() / ".hermes"
VOICE_MODE_PATH = HERMES_HOME / "gateway_voice_mode.json"

# Valid voice modes
VALID_MODES = {"voice_only", "text_only", "all"}

# Fallback home chat_id resolver (reads from gateway_state.json)
GATEWAY_STATE_PATH = HERMES_HOME / "gateway_state.json"


def _get_home_chat_id() -> Optional[str]:
    """Try to resolve the home Telegram chat_id from gateway_state.json"""
    try:
        if GATEWAY_STATE_PATH.exists():
            data = json.loads(GATEWAY_STATE_PATH.read_text())
            # gateway_state.json maps platform -> chat_id
            return data.get("telegram", {}).get("home_chat_id")
    except Exception:
        pass
    return None


def _load_modes() -> dict:
    try:
        return json.loads(VOICE_MODE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_modes(modes: dict) -> None:
    VOICE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_MODE_PATH.write_text(json.dumps(modes, indent=2))


def voice_mode_tool(mode: str, chat_id: Optional[str] = None) -> str:
    """
    Set or read the voice mode for a chat.

    Args:
        mode: One of "voice_only", "text_only", "all".
              - voice_only: Sydney speaks, no text sent (TTS + skip text)
              - text_only:  Friday sends text only, no TTS
              - all:        Both TTS and text (default fallback)
        chat_id: The target chat ID. Defaults to the Telegram home channel.

    Returns:
        JSON string with success status and current mode.
    """
    if mode not in VALID_MODES:
        return json.dumps({
            "success": False,
            "error": f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(VALID_MODES))}"
        }, ensure_ascii=False)

    # Resolve chat_id
    target_chat_id = chat_id
    if not target_chat_id:
        target_chat_id = _get_home_chat_id()
    if not target_chat_id:
        return json.dumps({
            "success": False,
            "error": "Could not resolve chat_id. Please pass it explicitly: voice_mode_tool(mode='voice_only', chat_id='12345')"
        }, ensure_ascii=False)

    modes = _load_modes()
    modes[target_chat_id] = mode
    _save_modes(modes)

    logger.info("[voice_mode_tool] Set voice mode for %s -> %s", target_chat_id, mode)

    return json.dumps({
        "success": True,
        "chat_id": target_chat_id,
        "mode": mode,
        "note": f"Voice mode set to '{mode}' for chat {target_chat_id}. This affects the NEXT response."
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry

VOICE_MODE_SCHEMA = {
    "name": "voice_mode",
    "description": (
        "Set the voice mode for the current chat. Controls whether Friday/Sydney send text, "
        "voice, or both. Mode persists across messages until changed. "
        "Use 'voice_only' for Sydney (Sydney speaks, no text). "
        "Use 'text_only' for Friday (Friday sends text only, no voice). "
        "Use 'all' for both TTS and text. "
        "Example: voice_mode_tool(mode='voice_only') at the START of a response."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["voice_only", "text_only", "all"],
                "description": "Voice mode: 'voice_only' (Sydney speaks, no text), 'text_only' (Friday text only), 'all' (both TTS and text)."
            },
            "chat_id": {
                "type": "string",
                "description": "Optional. The chat ID to set the mode for. Defaults to the Telegram home channel. Example: '5908295481'"
            }
        },
        "required": ["mode"]
    }
}

registry.register(
    name="voice_mode",
    toolset="core",
    schema=VOICE_MODE_SCHEMA,
    handler=voice_mode_tool,
)
