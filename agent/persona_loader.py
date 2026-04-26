"""Single source of truth for loading the active persona's system prompt.

This replaces the duplicated logic that was in both
agent/message_builder.py and run_agent.py.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_active_system_prompt(
    hermes_home: Path,
    persona_name: str = "friday",
    skip_context_files: bool = False,
) -> Optional[str]:
    """Load the system prompt for the active persona.

    Args:
        hermes_home: Path to ~/.hermes directory.
        persona_name: Name of the active persona ('friday', 'sydney', etc).
        skip_context_files: If True, return None (context loading disabled).

    Returns:
        The system prompt content, or None if not found or disabled.
    """
    if skip_context_files:
        return None

    if persona_name == "sydney":
        sydney_path = hermes_home / "SYDNEY.md"
        if sydney_path.exists():
            try:
                content = sydney_path.read_text(encoding="utf-8").strip()
                if content:
                    logger.debug("Loaded Sydney persona from %s", sydney_path)
                    return content
            except Exception as e:
                logger.warning("Failed to load SYDNEY.md: %s", e)

    # Default: load SOUL.md (for 'friday' or any unknown persona)
    soul_path = hermes_home / "SOUL.md"
    if soul_path.exists():
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Loaded Friday persona from %s", soul_path)
                return content
        except Exception as e:
            logger.warning("Failed to load SOUL.md: %s", e)

    return None
