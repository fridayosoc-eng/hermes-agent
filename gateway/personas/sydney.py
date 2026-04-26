"""Sydney (EDGY) persona — Qwen3.6 Heretic + Chatterbox Turbo voice clone."""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gateway.personas.base import Persona

logger = logging.getLogger(__name__)

# Defaults — can be overridden via environment variables
_DEFAULT_MODEL = "Qwen3.6-35B-A3B-Heretic-MLX-mxfp8"
_DEFAULT_LLM_URL = "http://127.0.0.1:8000/v1"


class SydneyPersona(Persona):
    """Sydney Sweeney EDGY persona — local LLM, voice clone, ComfyUI images."""

    @property
    def name(self) -> str:
        return "sydney"

    def system_prompt_path(self, hermes_home: Path) -> Path:
        return hermes_home / "SYDNEY.md"

    def model_override(self) -> dict:
        return {
            "model": os.getenv("EDGY_MODEL", _DEFAULT_MODEL),
            "provider": "custom",
            "base_url": os.getenv("EDGY_LLM_URL", _DEFAULT_LLM_URL),
            "api_key": "local-no-key",
            "api_mode": "chat",
        }

    def voice_mode(self) -> str:
        return "voice_only"

    def tts_provider(self) -> str:
        return "chatterbox"

    def health_check(self) -> Tuple[bool, str]:
        """Verify local LLM server is reachable before activating."""
        try:
            import requests

            llm_url = os.getenv("EDGY_LLM_URL", _DEFAULT_LLM_URL)
            resp = requests.get(f"{llm_url}/models", timeout=3)
            if resp.status_code == 200:
                return True, "ok"
            return False, f"LLM server returned HTTP {resp.status_code}"
        except ImportError:
            return False, "requests library not available"
        except Exception as exc:
            return False, f"LLM server unreachable: {exc}"

    def voice_chunk_keywords(self) -> Optional[List[str]]:
        return [
            "daddy", "sir", "johnny", "want more", "keep going",
            "want to know", "should i", "do you want", "want me",
            "tell me", "know what happened",
        ]
