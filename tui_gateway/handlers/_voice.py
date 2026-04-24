# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── voice ──────────────────────────────────────────────────────────

# ── Methods: voice ───────────────────────────────────────────────────


@method("voice.toggle")
def _(rid, params: dict) -> dict:
    action = params.get("action", "status")
    if action == "status":
        env = os.environ.get("HERMES_VOICE", "").strip()
        if env in {"0", "1"}:
            return _ok(rid, {"enabled": env == "1"})
        return _ok(
            rid,
            {
                "enabled": bool(
                    _load_cfg().get("display", {}).get("voice_enabled", False)
                )
            },
        )
    if action in ("on", "off"):
        enabled = action == "on"
        os.environ["HERMES_VOICE"] = "1" if enabled else "0"
        _write_config_key("display.voice_enabled", enabled)
        return _ok(rid, {"enabled": action == "on"})
    return _err(rid, 4013, f"unknown voice action: {action}")


@method("voice.record")
def _(rid, params: dict) -> dict:
    action = params.get("action", "start")
    try:
        if action == "start":
            from hermes_cli.voice import start_recording

            start_recording()
            return _ok(rid, {"status": "recording"})
        if action == "stop":
            from hermes_cli.voice import stop_and_transcribe

            return _ok(rid, {"text": stop_and_transcribe() or ""})
        return _err(rid, 4019, f"unknown voice action: {action}")
    except ImportError:
        return _err(
            rid, 5025, "voice module not available — install audio dependencies"
        )
    except Exception as e:
        return _err(rid, 5025, str(e))


@method("voice.tts")
def _(rid, params: dict) -> dict:
    text = params.get("text", "")
    if not text:
        return _err(rid, 4020, "text required")
    try:
        from hermes_cli.voice import speak_text

        threading.Thread(target=speak_text, args=(text,), daemon=True).start()
        return _ok(rid, {"status": "speaking"})
    except ImportError:
        return _err(rid, 5026, "voice module not available")
    except Exception as e:
        return _err(rid, 5026, str(e))


