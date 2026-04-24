# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── config_io ──────────────────────────────────────────────────────────

# ── Config I/O ────────────────────────────────────────────────────────


def _load_cfg() -> dict:
    global _cfg_cache, _cfg_mtime
    try:
        import yaml

        p = _hermes_home / "config.yaml"
        mtime = p.stat().st_mtime if p.exists() else None
        with _cfg_lock:
            if _cfg_cache is not None and _cfg_mtime == mtime:
                return copy.deepcopy(_cfg_cache)
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        with _cfg_lock:
            _cfg_cache = copy.deepcopy(data)
            _cfg_mtime = mtime
        return data
    except Exception:
        pass
    return {}


def _save_cfg(cfg: dict):
    global _cfg_cache, _cfg_mtime
    import yaml

    path = _hermes_home / "config.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f)
    with _cfg_lock:
        _cfg_cache = copy.deepcopy(cfg)
        try:
            _cfg_mtime = path.stat().st_mtime
        except Exception:
            _cfg_mtime = None


