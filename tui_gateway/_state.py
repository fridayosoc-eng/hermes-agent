import atexit
import concurrent.futures
import copy
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli.env_loader import load_hermes_dotenv

logger = logging.getLogger(__name__)

_hermes_home = get_hermes_home()
load_hermes_dotenv(
    hermes_home=_hermes_home, project_env=Path(__file__).parent.parent / ".env"
)

try:
    from hermes_cli.banner import prefetch_update_check

    prefetch_update_check()
except Exception:
    pass


_sessions: dict[str, dict] = {}
_methods: dict[str, callable] = {}
_pending: dict[str, tuple[str, threading.Event]] = {}
_answers: dict[str, str] = {}
_db = None
_db_error: str | None = None
_stdout_lock = threading.Lock()
_cfg_lock = threading.Lock()
_cfg_cache: dict | None = None
_cfg_mtime: float | None = None
_SLASH_WORKER_TIMEOUT_S = max(
    5.0, float(os.environ.get("HERMES_TUI_SLASH_TIMEOUT_S", "45") or 45)
)

# ── Async RPC dispatch (#12546) ──────────────────────────────────────
# A handful of handlers block the dispatcher loop in entry.py for seconds
# to minutes (slash.exec, cli.exec, shell.exec, session.resume,
# session.branch, skills.manage).  While they're running, inbound RPCs —
# notably approval.respond and session.interrupt — sit unread in the
# stdin pipe.  We route only those slow handlers onto a small thread pool;
# everything else stays on the main thread so ordering stays sane for the
# fast path.  write_json is already _stdout_lock-guarded, so concurrent
# response writes are safe.
_LONG_HANDLERS = frozenset(
    {
        "cli.exec",
        "session.branch",
        "session.resume",
        "shell.exec",
        "skills.manage",
        "slash.exec",
    }
)

_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(2, int(os.environ.get("HERMES_TUI_RPC_POOL_WORKERS", "4") or 4)),
    thread_name_prefix="tui-rpc",
)
atexit.register(lambda: _pool.shutdown(wait=False, cancel_futures=True))

# Reserve real stdout for JSON-RPC only; redirect Python's stdout to stderr
# so stray print() from libraries/tools becomes harmless gateway.stderr instead
# of corrupting the JSON protocol.
_real_stdout = sys.stdout
sys.stdout = sys.stderr


__all__ = [
    "_sessions", "_methods", "_pending", "_answers",
    "_db", "_db_error", "_stdout_lock", "_cfg_lock",
    "_cfg_cache", "_cfg_mtime", "_SLASH_WORKER_TIMEOUT_S",
    "_LONG_HANDLERS", "_pool", "_real_stdout",
    "method",
]


# ── Global state ──────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}
_methods: dict[str, callable] = {}
_pending: dict[str, tuple[str, threading.Event]] = {}
_answers: dict[str, str] = {}
_db = None
_db_error: str | None = None
_stdout_lock = threading.Lock()
_cfg_lock = threading.Lock()
_cfg_cache: dict | None = None
_cfg_mtime: float | None = None
_SLASH_WORKER_TIMEOUT_S = max(
    5.0, float(os.environ.get("HERMES_TUI_SLASH_TIMEOUT_S", "45") or 45)
)

_LONG_HANDLERS = frozenset(
    {
        "cli.exec",
        "session.branch",
        "session.resume",
        "shell.exec",
        "skills.manage",
        "slash.exec",
    }
)

_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(2, int(os.environ.get("HERMES_TUI_RPC_POOL_WORKERS", "4") or 4)),
    thread_name_prefix="tui-rpc",
)
atexit.register(lambda: _pool.shutdown(wait=False, cancel_futures=True))

# Reserve real stdout for JSON-RPC only
_real_stdout = sys.stdout
sys.stdout = sys.stderr


def method(name: str):
    def dec(fn):
        _methods[name] = fn
        return fn
    return dec
