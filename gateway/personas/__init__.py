"""Persona system — encapsulates all behavioral config for a given AI personality.

Replaces the scattered edgy_state.py + inline if/else checks with a clean
object-oriented approach where each persona is a single source of truth for:
- System prompt path
- Model configuration
- TTS provider
- Voice mode
- Cache signature
"""

from gateway.personas.base import Persona
from gateway.personas.manager import PersonaManager

__all__ = ["Persona", "PersonaManager"]
