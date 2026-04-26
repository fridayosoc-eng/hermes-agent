"""PersonaManager — session-scoped persona state (replaces edgy_state.py).

Thread-safe, in-memory only. Each session maps to exactly one active persona
(or the default Friday persona if none is set).
"""

import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from gateway.personas.base import Persona
from gateway.personas.friday import FridayPersona
from gateway.personas.sydney import SydneyPersona

logger = logging.getLogger(__name__)


class PersonaManager:
    """Manages active persona per session.

    Replaces:
      - gateway/edgy_state.py (_edgy_sessions, _edgy_chat_ids, threading.local)
      - gateway/run.py _session_model_overrides for persona-driven model routing
      - Monkey-patched agent._edgy_mode for cache invalidation
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, Persona] = {}  # session_key → active persona
        self._default = FridayPersona()
        self._registry: Dict[str, Persona] = {
            "friday": self._default,
            "sydney": SydneyPersona(),
        }

    def register(self, name: str, persona: Persona) -> None:
        """Register a new persona type."""
        with self._lock:
            self._registry[name] = persona
        logger.info("Persona registered: %s", name)

    def activate(self, session_key: str, persona_name: str) -> Persona:
        """Switch session to a named persona. Returns the persona for health check."""
        persona = self._registry.get(persona_name)
        if not persona:
            raise ValueError(f"Unknown persona: {persona_name}")
        with self._lock:
            self._sessions[session_key] = persona
        logger.info("Persona activated: session=%s persona=%s", session_key, persona_name)
        return persona

    def deactivate(self, session_key: str) -> Persona:
        """Revert session to default persona."""
        with self._lock:
            self._sessions.pop(session_key, None)
        logger.info("Persona deactivated: session=%s -> friday", session_key)
        return self._default

    def current(self, session_key: str) -> Persona:
        """Get active persona for a session. Returns default if none set."""
        with self._lock:
            return self._sessions.get(session_key, self._default)

    def is_active(self, session_key: str, persona_name: str) -> bool:
        """Check if a specific persona is active for a session."""
        with self._lock:
            p = self._sessions.get(session_key)
            return p is not None and p.name == persona_name

    def active_persona_name(self, session_key: str) -> str:
        """Get the name of the active persona for a session."""
        return self.current(session_key).name

    def list_personas(self) -> list:
        """List all registered persona names."""
        return list(self._registry.keys())
