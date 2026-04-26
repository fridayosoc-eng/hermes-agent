"""Default Friday persona."""

from pathlib import Path

from gateway.personas.base import Persona


class FridayPersona(Persona):
    """The default AI assistant persona — Friday."""

    @property
    def name(self) -> str:
        return "friday"

    def system_prompt_path(self, hermes_home: Path) -> Path:
        return hermes_home / "SOUL.md"
