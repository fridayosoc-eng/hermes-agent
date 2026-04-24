"""Edgy mode v2 shared state — in-memory, no file I/O.

Both run.py (command handlers) and base.py (text suppression) read/write this.
This replaces the old edgy_mode.json file-based approach.

Keys are stored BOTH by session_key (for model override matching)
and by str(chat_id) (for base.py text suppression which doesn't have session context).

A threading.local is used so prompt_builder.py knows to load SYDNEY.md
for the current agent creation context.
"""
import threading
from typing import Dict, Optional

# session_key -> bool (primary, matches _session_model_overrides keys)
_edgy_sessions: Dict[str, bool] = {}

# chat_id (as str) -> bool (secondary, for base.py lookups without session store)
_edgy_chat_ids: Dict[str, bool] = {}

# Thread-local: set before agent creation so prompt_builder knows to load SYDNEY.md
_local = threading.local()


def is_edgy_session(session_key: str = "", chat_id: str = "") -> bool:
    """Check if a session is in edgy mode. Accept either key type."""
    if session_key and _edgy_sessions.get(session_key):
        return True
    if chat_id and _edgy_chat_ids.get(chat_id):
        return True
    return False


def set_edgy_session(session_key: str, chat_id: str = "", enabled: bool = True) -> None:
    """Set or clear edgy mode for a session."""
    if enabled:
        _edgy_sessions[session_key] = True
        if chat_id:
            _edgy_chat_ids[chat_id] = True
    else:
        _edgy_sessions.pop(session_key, None)
        if chat_id:
            _edgy_chat_ids.pop(chat_id, None)


def set_current_thread_edgy(enabled: bool) -> None:
    """Mark the current thread as running an edgy agent (for prompt_builder)."""
    _local.is_edgy = enabled


def is_current_thread_edgy() -> bool:
    """Check if the current thread is running an edgy agent."""
    return getattr(_local, 'is_edgy', False)
