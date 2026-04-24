# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

import atexit
import concurrent.futures
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
from tui_gateway.render import make_stream_renderer, render_diff, render_message

from tui_gateway._state import (
    _methods, _sessions, _pending, _answers, _db, _db_error,
    _stdout_lock, _cfg_lock, _cfg_cache, _cfg_mtime,
    _SLASH_WORKER_TIMEOUT_S, _pool, _real_stdout, sys, method,
    _LONG_HANDLERS,
)

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

# ── Slash worker ───────────────────────────────────────────────────────

class _SlashWorker:
    """Persistent HermesCLI subprocess for slash commands."""

    def __init__(self, session_key: str, model: str):
        self._lock = threading.Lock()
        self._seq = 0
        self.stderr_tail: list[str] = []
        self.stdout_queue: queue.Queue[dict | None] = queue.Queue()

        argv = [
            sys.executable,
            "-m",
            "tui_gateway.slash_worker",
            "--session-key",
            session_key,
        ]
        if model:
            argv += ["--model", model]

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stdout(self):
        for line in self.proc.stdout or []:
            try:
                self.stdout_queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.stdout_queue.put(None)

    def _drain_stderr(self):
        for line in self.proc.stderr or []:
            if text := line.rstrip("\n"):
                self.stderr_tail = (self.stderr_tail + [text])[-80:]

    def run(self, command: str) -> str:
        if self.proc.poll() is not None:
            raise RuntimeError("slash worker exited")

        with self._lock:
            self._seq += 1
            rid = self._seq
            self.proc.stdin.write(json.dumps({"id": rid, "command": command}) + "\n")
            self.proc.stdin.flush()

            while True:
                try:
                    msg = self.stdout_queue.get(timeout=_SLASH_WORKER_TIMEOUT_S)
                except queue.Empty:
                    raise RuntimeError("slash worker timed out")
                if msg is None:
                    break
                if msg.get("id") != rid:
                    continue
                if not msg.get("ok"):
                    raise RuntimeError(msg.get("error", "slash worker failed"))
                return str(msg.get("output", "")).rstrip()

            raise RuntimeError(
                f"slash worker closed pipe{': ' + chr(10).join(self.stderr_tail[-8:]) if self.stderr_tail else ''}"
            )

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=1)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


atexit.register(
    lambda: [
        s.get("slash_worker") and s["slash_worker"].close() for s in _sessions.values()
    ]
)


atexit.register(
    lambda: [
        s.get("slash_worker") and s["slash_worker"].close() for s in _sessions.values()
    ]
)

# ── Plumbing ───────────────────────────────────────────────────────────

# ── Plumbing ──────────────────────────────────────────────────────────


def _get_db():
    global _db, _db_error
    if _db is None:
        from hermes_state import SessionDB

        try:
            _db = SessionDB()
            _db_error = None
        except Exception as exc:
            _db_error = str(exc)
            logger.warning(
                "TUI session store unavailable — continuing without state.db features: %s",
                exc,
            )
            return None
    return _db


def _db_unavailable_error(rid, *, code: int):
    detail = _db_error or "state.db unavailable"
    return _err(rid, code, f"state.db unavailable: {detail}")


def write_json(obj: dict) -> bool:
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    try:
        with _stdout_lock:
            _real_stdout.write(line)
            _real_stdout.flush()
        return True
    except BrokenPipeError:
        return False


def _emit(event: str, sid: str, payload: dict | None = None):
    params = {"type": event, "session_id": sid}
    if payload is not None:
        params["payload"] = payload
    write_json({"jsonrpc": "2.0", "method": "event", "params": params})


def _status_update(sid: str, kind: str, text: str | None = None):
    body = (text if text is not None else kind).strip()
    if not body:
        return
    _emit(
        "status.update",
        sid,
        {"kind": kind if text is not None else "status", "text": body},
    )


def _estimate_image_tokens(width: int, height: int) -> int:
    """Very rough UI estimate for image prompt cost.

    Uses 512px tiles at ~85 tokens/tile as a lightweight cross-provider hint.
    This is intentionally approximate and only used for attachment display.
    """
    if width <= 0 or height <= 0:
        return 0
    return max(1, (width + 511) // 512) * max(1, (height + 511) // 512) * 85


def _image_meta(path: Path) -> dict:
    meta = {"name": path.name}
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
        meta["width"] = int(width)
        meta["height"] = int(height)
        meta["token_estimate"] = _estimate_image_tokens(int(width), int(height))
    except Exception:
        pass
    return meta


def _ok(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def method(name: str):
    def dec(fn):
        _methods[name] = fn
        return fn

    return dec


def handle_request(req: dict) -> dict | None:
    fn = _methods.get(req.get("method", ""))
    if not fn:
        return _err(req.get("id"), -32601, f"unknown method: {req.get('method')}")
    return fn(req.get("id"), req.get("params", {}))


def dispatch(req: dict) -> dict | None:
    """Route inbound RPCs — long handlers to the pool, everything else inline.

    Returns a response dict when handled inline. Returns None when the
    handler was scheduled on the pool; the worker writes its own
    response via write_json when done.
    """
    if req.get("method") not in _LONG_HANDLERS:
        return handle_request(req)

    def run():
        try:
            resp = handle_request(req)
        except Exception as exc:
            resp = _err(req.get("id"), -32000, f"handler error: {exc}")
        if resp is not None:
            write_json(resp)

    _pool.submit(run)

    return None


def _wait_agent(session: dict, rid: str, timeout: float = 30.0) -> dict | None:
    ready = session.get("agent_ready")
    if ready is not None and not ready.wait(timeout=timeout):
        return _err(rid, 5032, "agent initialization timed out")
    err = session.get("agent_error")
    return _err(rid, 5032, err) if err else None


def _sess_nowait(params, rid):
    s = _sessions.get(params.get("session_id") or "")
    return (s, None) if s else (None, _err(rid, 4001, "session not found"))


def _sess(params, rid):
    s, err = _sess_nowait(params, rid)
    return (None, err) if err else (s, _wait_agent(s, rid))


def _normalize_completion_path(path_part: str) -> str:
    expanded = os.path.expanduser(path_part)
    if os.name != "nt":
        normalized = expanded.replace("\\", "/")
        if (
            len(normalized) >= 3
            and normalized[1] == ":"
            and normalized[2] == "/"
            and normalized[0].isalpha()
        ):
            return f"/mnt/{normalized[0].lower()}/{normalized[3:]}"
    return expanded

