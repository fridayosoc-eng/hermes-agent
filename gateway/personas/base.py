"""Abstract base class for AI personas."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple


class Persona(ABC):
    """A persona encapsulates ALL behavioral differences between personalities.

    Instead of scattered if/else checks across 6 files, each persona
    is a single object that answers: what system prompt? what model?
    what TTS voice? what voice mode?
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique persona identifier (e.g. 'friday', 'sydney')."""

    @abstractmethod
    def system_prompt_path(self, hermes_home: Path) -> Path:
        """Path to the system prompt markdown file."""

    def model_override(self) -> dict:
        """Model override dict. Return {} to use the default model."""
        return {}

    def voice_mode(self) -> str:
        """Voice delivery mode: 'off', 'voice_only', 'text_only', 'both'."""
        return "off"

    def tts_provider(self) -> str:
        """TTS provider name for this persona."""
        return "default"

    def should_suppress_text(self) -> bool:
        """Whether to suppress text reply after sending voice."""
        return self.voice_mode() == "voice_only"

    def health_check(self) -> Tuple[bool, str]:
        """Validate external dependencies. Returns (ok, message)."""
        return True, "ok"

    def cache_signature(self) -> str:
        """Unique string for agent cache invalidation."""
        return self.name

    def voice_chunk_keywords(self) -> Optional[List[str]]:
        """Keywords for voice chunking. None = use default sentence splitting."""
        return None
