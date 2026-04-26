"""Shared HTTP session pool for all tools.

Avoids creating a new TCP+TLS connection for every HTTP request.
Tools should use get_sync_session() instead of bare requests.post/get.
"""

import atexit
import logging
import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

_sync_session: Optional[requests.Session] = None
_sync_lock = threading.Lock()

# Pool defaults
_POOL_CONNECTIONS = 10
_POOL_MAXSIZE = 10
_MAX_RETRIES = 2


def get_sync_session() -> requests.Session:
    """Get or create a shared requests.Session with connection pooling.

    Thread-safe. The session is reused across all callers and closed
    automatically on process exit via atexit.
    """
    global _sync_session
    if _sync_session is not None:
        return _sync_session

    with _sync_lock:
        # Double-checked locking
        if _sync_session is not None:
            return _sync_session

        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=_POOL_CONNECTIONS,
            pool_maxsize=_POOL_MAXSIZE,
            max_retries=_MAX_RETRIES,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _sync_session = session
        atexit.register(_cleanup)
        logger.debug("Shared HTTP session pool initialized (conns=%d, max=%d)",
                      _POOL_CONNECTIONS, _POOL_MAXSIZE)
        return _sync_session


def _cleanup() -> None:
    """Close the shared session on process exit."""
    global _sync_session
    if _sync_session is not None:
        try:
            _sync_session.close()
        except Exception:
            pass
        _sync_session = None
