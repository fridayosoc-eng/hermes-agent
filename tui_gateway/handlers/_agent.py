# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── agent ──────────────────────────────────────────────────────────

# ── Agent factory ────────────────────────────────────────────────────


def resolve_skin() -> dict:
    try:
        from hermes_cli.skin_engine import init_skin_from_config, get_active_skin

        init_skin_from_config(_load_cfg())
        skin = get_active_skin()
        return {
            "name": skin.name,
            "colors": skin.colors,
            "branding": skin.branding,
            "banner_logo": skin.banner_logo,
            "banner_hero": skin.banner_hero,
            "tool_prefix": skin.tool_prefix,
            "help_header": (skin.branding or {}).get("help_header", ""),
        }
    except Exception:
        return {}


def _resolve_model() -> str:
    env = os.environ.get("HERMES_MODEL", "")
    if env:
        return env
    m = _load_cfg().get("model", "")
    if isinstance(m, dict):
        return m.get("default", "")
    if isinstance(m, str) and m:
        return m
    return "anthropic/claude-sonnet-4"


def _write_config_key(key_path: str, value):
    cfg = _load_cfg()
    current = cfg
    keys = key_path.split(".")
    for key in keys[:-1]:
        if key not in current or not isinstance(current.get(key), dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
    _save_cfg(cfg)


_STATUSBAR_MODES = frozenset({"off", "top", "bottom"})


def _coerce_statusbar(raw) -> str:
    if raw is False:
        return "off"
    if isinstance(raw, str) and (s := raw.strip().lower()) in _STATUSBAR_MODES:
        return s
    return "top"


def _load_reasoning_config() -> dict | None:
    from hermes_constants import parse_reasoning_effort

    effort = str(_load_cfg().get("agent", {}).get("reasoning_effort", "") or "").strip()
    return parse_reasoning_effort(effort)


def _load_service_tier() -> str | None:
    raw = (
        str(_load_cfg().get("agent", {}).get("service_tier", "") or "").strip().lower()
    )
    if not raw or raw in {"normal", "default", "standard", "off", "none"}:
        return None
    if raw in {"fast", "priority", "on"}:
        return "priority"
    return None


def _load_show_reasoning() -> bool:
    return bool(_load_cfg().get("display", {}).get("show_reasoning", False))


def _load_tool_progress_mode() -> str:
    raw = _load_cfg().get("display", {}).get("tool_progress", "all")
    if raw is False:
        return "off"
    if raw is True:
        return "all"
    mode = str(raw or "all").strip().lower()
    return mode if mode in {"off", "new", "all", "verbose"} else "all"


def _load_enabled_toolsets() -> list[str] | None:
    try:
        from hermes_cli.config import load_config
        from hermes_cli.tools_config import _get_platform_tools

        enabled = sorted(
            _get_platform_tools(load_config(), "cli", include_default_mcp_servers=False)
        )
        return enabled or None
    except Exception:
        return None


def _session_tool_progress_mode(sid: str) -> str:
    return str(_sessions.get(sid, {}).get("tool_progress_mode", "all") or "all")


def _tool_progress_enabled(sid: str) -> bool:
    return _session_tool_progress_mode(sid) != "off"


def _restart_slash_worker(session: dict):
    worker = session.get("slash_worker")
    if worker:
        try:
            worker.close()
        except Exception:
            pass
    try:
        session["slash_worker"] = _SlashWorker(
            session["session_key"],
            getattr(session.get("agent"), "model", _resolve_model()),
        )
    except Exception:
        session["slash_worker"] = None


def _persist_model_switch(result) -> None:
    from hermes_cli.config import save_config

    cfg = _load_cfg()
    model_cfg = cfg.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        cfg["model"] = model_cfg

    model_cfg["default"] = result.new_model
    model_cfg["provider"] = result.target_provider
    if result.base_url:
        model_cfg["base_url"] = result.base_url
    else:
        model_cfg.pop("base_url", None)
    save_config(cfg)


def _apply_model_switch(sid: str, session: dict, raw_input: str) -> dict:
    from hermes_cli.model_switch import parse_model_flags, switch_model
    from hermes_cli.runtime_provider import resolve_runtime_provider

    model_input, explicit_provider, persist_global = parse_model_flags(raw_input)
    if not model_input:
        raise ValueError("model value required")

    agent = session.get("agent")
    if agent:
        current_provider = getattr(agent, "provider", "") or ""
        current_model = getattr(agent, "model", "") or ""
        current_base_url = getattr(agent, "base_url", "") or ""
        current_api_key = getattr(agent, "api_key", "") or ""
    else:
        runtime = resolve_runtime_provider(requested=None)
        current_provider = str(runtime.get("provider", "") or "")
        current_model = _resolve_model()
        current_base_url = str(runtime.get("base_url", "") or "")
        current_api_key = str(runtime.get("api_key", "") or "")

    result = switch_model(
        raw_input=model_input,
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=current_base_url,
        current_api_key=current_api_key,
        is_global=persist_global,
        explicit_provider=explicit_provider,
    )
    if not result.success:
        raise ValueError(result.error_message or "model switch failed")

    if agent:
        agent.switch_model(
            new_model=result.new_model,
            new_provider=result.target_provider,
            api_key=result.api_key,
            base_url=result.base_url,
            api_mode=result.api_mode,
        )
        _restart_slash_worker(session)
        _emit("session.info", sid, _session_info(agent))

    os.environ["HERMES_MODEL"] = result.new_model
    # Keep the process-level provider env var in sync with the user's explicit
    # choice so any ambient re-resolution (credential pool refresh, compressor
    # rebuild, aux clients) resolves to the new provider instead of the
    # original one persisted in config or env.
    if result.target_provider:
        os.environ["HERMES_INFERENCE_PROVIDER"] = result.target_provider
    if persist_global:
        _persist_model_switch(result)
    return {"value": result.new_model, "warning": result.warning_message or ""}


def _compress_session_history(
    session: dict, focus_topic: str | None = None
) -> tuple[int, dict]:
    from agent.model_metadata import estimate_messages_tokens_rough

    agent = session["agent"]
    history = list(session.get("history", []))
    if len(history) < 4:
        return 0, _get_usage(agent)
    approx_tokens = estimate_messages_tokens_rough(history)
    compressed, _ = agent._compress_context(
        history,
        getattr(agent, "_cached_system_prompt", "") or "",
        approx_tokens=approx_tokens,
        focus_topic=focus_topic or None,
    )
    session["history"] = compressed
    session["history_version"] = int(session.get("history_version", 0)) + 1
    return len(history) - len(compressed), _get_usage(agent)


def _get_usage(agent) -> dict:
    g = lambda k, fb=None: getattr(agent, k, 0) or (getattr(agent, fb, 0) if fb else 0)
    usage = {
        "model": getattr(agent, "model", "") or "",
        "input": g("session_input_tokens", "session_prompt_tokens"),
        "output": g("session_output_tokens", "session_completion_tokens"),
        "cache_read": g("session_cache_read_tokens"),
        "cache_write": g("session_cache_write_tokens"),
        "prompt": g("session_prompt_tokens"),
        "completion": g("session_completion_tokens"),
        "total": g("session_total_tokens"),
        "calls": g("session_api_calls"),
    }
    comp = getattr(agent, "context_compressor", None)
    if comp:
        ctx_used = getattr(comp, "last_prompt_tokens", 0) or usage["total"] or 0
        ctx_max = getattr(comp, "context_length", 0) or 0
        if ctx_max:
            usage["context_used"] = ctx_used
            usage["context_max"] = ctx_max
            usage["context_percent"] = max(0, min(100, round(ctx_used / ctx_max * 100)))
        usage["compressions"] = getattr(comp, "compression_count", 0) or 0
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

        cost = estimate_usage_cost(
            usage["model"],
            CanonicalUsage(
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                cache_read_tokens=usage["cache_read"],
                cache_write_tokens=usage["cache_write"],
            ),
            provider=getattr(agent, "provider", None),
            base_url=getattr(agent, "base_url", None),
        )
        usage["cost_status"] = cost.status
        if cost.amount_usd is not None:
            usage["cost_usd"] = float(cost.amount_usd)
    except Exception:
        pass
    return usage


def _probe_credentials(agent) -> str:
    """Light credential check at session creation — returns warning or ''."""
    try:
        key = getattr(agent, "api_key", "") or ""
        provider = getattr(agent, "provider", "") or ""
        if not key or key == "no-key-required":
            return f"No API key configured for provider '{provider}'. First message will fail."
    except Exception:
        pass
    return ""


def _session_info(agent) -> dict:
    info: dict = {
        "model": getattr(agent, "model", ""),
        "tools": {},
        "skills": {},
        "cwd": os.getcwd(),
        "version": "",
        "release_date": "",
        "update_behind": None,
        "update_command": "",
        "usage": _get_usage(agent),
    }
    try:
        from hermes_cli import __version__, __release_date__

        info["version"] = __version__
        info["release_date"] = __release_date__
    except Exception:
        pass
    try:
        from model_tools import get_toolset_for_tool

        for t in getattr(agent, "tools", []) or []:
            name = t["function"]["name"]
            info["tools"].setdefault(get_toolset_for_tool(name) or "other", []).append(
                name
            )
    except Exception:
        pass
    try:
        from hermes_cli.banner import get_available_skills

        info["skills"] = get_available_skills()
    except Exception:
        pass
    try:
        from tools.mcp_tool import get_mcp_status

        info["mcp_servers"] = get_mcp_status()
    except Exception:
        info["mcp_servers"] = []
    try:
        from hermes_cli.banner import get_update_result
        from hermes_cli.config import recommended_update_command

        info["update_behind"] = get_update_result(timeout=0.5)
        info["update_command"] = recommended_update_command()
    except Exception:
        pass
    return info


def _tool_ctx(name: str, args: dict) -> str:
    try:
        from agent.display import build_tool_preview

        return build_tool_preview(name, args, max_len=80) or ""
    except Exception:
        return ""


def _fmt_tool_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{round(seconds)}s"
    mins, secs = divmod(int(round(seconds)), 60)
    return f"{mins}m {secs}s" if secs else f"{mins}m"


def _count_list(obj: object, *path: str) -> int | None:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return len(cur) if isinstance(cur, list) else None


def _tool_summary(name: str, result: str, duration_s: float | None) -> str | None:
    try:
        data = json.loads(result)
    except Exception:
        data = None

    dur = _fmt_tool_duration(duration_s)
    suffix = f" in {dur}" if dur else ""
    text = None

    if name == "web_search" and isinstance(data, dict):
        n = _count_list(data, "data", "web")
        if n is not None:
            text = f"Did {n} {'search' if n == 1 else 'searches'}"

    elif name == "web_extract" and isinstance(data, dict):
        n = _count_list(data, "results") or _count_list(data, "data", "results")
        if n is not None:
            text = f"Extracted {n} {'page' if n == 1 else 'pages'}"

    return f"{text or 'Completed'}{suffix}" if (text or dur) else None


def _on_tool_start(sid: str, tool_call_id: str, name: str, args: dict):
    session = _sessions.get(sid)
    if session is not None:
        try:
            from agent.display import capture_local_edit_snapshot

            snapshot = capture_local_edit_snapshot(name, args)
            if snapshot is not None:
                session.setdefault("edit_snapshots", {})[tool_call_id] = snapshot
        except Exception:
            pass
        session.setdefault("tool_started_at", {})[tool_call_id] = time.time()
    if _tool_progress_enabled(sid):
        _emit(
            "tool.start",
            sid,
            {"tool_id": tool_call_id, "name": name, "context": _tool_ctx(name, args)},
        )


def _on_tool_complete(sid: str, tool_call_id: str, name: str, args: dict, result: str):
    payload = {"tool_id": tool_call_id, "name": name}
    session = _sessions.get(sid)
    snapshot = None
    started_at = None
    if session is not None:
        snapshot = session.setdefault("edit_snapshots", {}).pop(tool_call_id, None)
        started_at = session.setdefault("tool_started_at", {}).pop(tool_call_id, None)
    duration_s = time.time() - started_at if started_at else None
    if duration_s is not None:
        payload["duration_s"] = duration_s
    summary = _tool_summary(name, result, duration_s)
    if summary:
        payload["summary"] = summary
    try:
        from agent.display import render_edit_diff_with_delta

        rendered: list[str] = []
        if render_edit_diff_with_delta(
            name,
            result,
            function_args=args,
            snapshot=snapshot,
            print_fn=rendered.append,
        ):
            payload["inline_diff"] = "\n".join(rendered)
    except Exception:
        pass
    if _tool_progress_enabled(sid) or payload.get("inline_diff"):
        _emit("tool.complete", sid, payload)


def _on_tool_progress(
    sid: str,
    event_type: str,
    name: str | None = None,
    preview: str | None = None,
    _args: dict | None = None,
    **_kwargs,
):
    if not _tool_progress_enabled(sid):
        return
    if event_type == "tool.started" and name:
        _emit("tool.progress", sid, {"name": name, "preview": preview or ""})
        return
    if event_type == "reasoning.available" and preview:
        _emit("reasoning.available", sid, {"text": str(preview)})
        return
    if event_type.startswith("subagent."):
        payload = {
            "goal": str(_kwargs.get("goal") or ""),
            "task_count": int(_kwargs.get("task_count") or 1),
            "task_index": int(_kwargs.get("task_index") or 0),
        }
        # Identity fields for the TUI spawn tree.  All optional — older
        # emitters that omit them fall back to flat rendering client-side.
        if _kwargs.get("subagent_id"):
            payload["subagent_id"] = str(_kwargs["subagent_id"])
        if _kwargs.get("parent_id"):
            payload["parent_id"] = str(_kwargs["parent_id"])
        if _kwargs.get("depth") is not None:
            payload["depth"] = int(_kwargs["depth"])
        if _kwargs.get("model"):
            payload["model"] = str(_kwargs["model"])
        if _kwargs.get("tool_count") is not None:
            payload["tool_count"] = int(_kwargs["tool_count"])
        if _kwargs.get("toolsets"):
            payload["toolsets"] = [str(t) for t in _kwargs["toolsets"]]
        # Per-branch rollups emitted on subagent.complete (features 1+2+4).
        for int_key in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "api_calls",
        ):
            val = _kwargs.get(int_key)
            if val is not None:
                try:
                    payload[int_key] = int(val)
                except (TypeError, ValueError):
                    pass
        if _kwargs.get("cost_usd") is not None:
            try:
                payload["cost_usd"] = float(_kwargs["cost_usd"])
            except (TypeError, ValueError):
                pass
        if _kwargs.get("files_read"):
            payload["files_read"] = [str(p) for p in _kwargs["files_read"]]
        if _kwargs.get("files_written"):
            payload["files_written"] = [str(p) for p in _kwargs["files_written"]]
        if _kwargs.get("output_tail"):
            payload["output_tail"] = list(_kwargs["output_tail"])  # list of dicts
        if name:
            payload["tool_name"] = str(name)
        if preview:
            payload["text"] = str(preview)
        if _kwargs.get("status"):
            payload["status"] = str(_kwargs["status"])
        if _kwargs.get("summary"):
            payload["summary"] = str(_kwargs["summary"])
        if _kwargs.get("duration_seconds") is not None:
            payload["duration_seconds"] = float(_kwargs["duration_seconds"])
        if preview and event_type == "subagent.tool":
            payload["tool_preview"] = str(preview)
            payload["text"] = str(preview)
        _emit(event_type, sid, payload)


def _agent_cbs(sid: str) -> dict:
    return dict(
        tool_start_callback=lambda tc_id, name, args: _on_tool_start(
            sid, tc_id, name, args
        ),
        tool_complete_callback=lambda tc_id, name, args, result: _on_tool_complete(
            sid, tc_id, name, args, result
        ),
        tool_progress_callback=lambda event_type, name=None, preview=None, args=None, **kwargs: _on_tool_progress(
            sid, event_type, name, preview, args, **kwargs
        ),
        tool_gen_callback=lambda name: _tool_progress_enabled(sid)
        and _emit("tool.generating", sid, {"name": name}),
        thinking_callback=lambda text: _emit("thinking.delta", sid, {"text": text}),
        reasoning_callback=lambda text: _emit("reasoning.delta", sid, {"text": text}),
        status_callback=lambda kind, text=None: _status_update(
            sid, str(kind), None if text is None else str(text)
        ),
        clarify_callback=lambda q, c: _block(
            "clarify.request", sid, {"question": q, "choices": c}
        ),
    )


def _wire_callbacks(sid: str):
    from tools.terminal_tool import set_sudo_password_callback
    from tools.skills_tool import set_secret_capture_callback

    set_sudo_password_callback(lambda: _block("sudo.request", sid, {}, timeout=120))

    def secret_cb(env_var, prompt, metadata=None):
        pl = {"prompt": prompt, "env_var": env_var}
        if metadata:
            pl["metadata"] = metadata
        val = _block("secret.request", sid, pl)
        if not val:
            return {
                "success": True,
                "stored_as": env_var,
                "validated": False,
                "skipped": True,
                "message": "skipped",
            }
        from hermes_cli.config import save_env_value_secure

        return {
            **save_env_value_secure(env_var, val),
            "skipped": False,
            "message": "ok",
        }

    set_secret_capture_callback(secret_cb)


def _resolve_personality_prompt(cfg: dict) -> str:
    """Resolve the active personality into a system prompt string."""
    name = (cfg.get("display", {}).get("personality", "") or "").strip().lower()
    if not name or name in ("default", "none", "neutral"):
        return ""
    try:
        from cli import load_cli_config

        personalities = load_cli_config().get("agent", {}).get("personalities", {})
    except Exception:
        try:
            from hermes_cli.config import load_config as _load_full_cfg

            personalities = _load_full_cfg().get("agent", {}).get("personalities", {})
        except Exception:
            personalities = cfg.get("agent", {}).get("personalities", {})
    pval = personalities.get(name)
    if pval is None:
        return ""
    return _render_personality_prompt(pval)


def _render_personality_prompt(value) -> str:
    if isinstance(value, dict):
        parts = [value.get("system_prompt", "")]
        if value.get("tone"):
            parts.append(f'Tone: {value["tone"]}')
        if value.get("style"):
            parts.append(f'Style: {value["style"]}')
        return "\n".join(p for p in parts if p)
    return str(value)


def _available_personalities(cfg: dict | None = None) -> dict:
    try:
        from cli import load_cli_config

        return load_cli_config().get("agent", {}).get("personalities", {}) or {}
    except Exception:
        try:
            from hermes_cli.config import load_config as _load_full_cfg

            return _load_full_cfg().get("agent", {}).get("personalities", {}) or {}
        except Exception:
            cfg = cfg or _load_cfg()
            return cfg.get("agent", {}).get("personalities", {}) or {}


def _validate_personality(value: str, cfg: dict | None = None) -> tuple[str, str]:
    raw = str(value or "").strip()
    name = raw.lower()
    if not name or name in ("none", "default", "neutral"):
        return "", ""

    personalities = _available_personalities(cfg)
    if name not in personalities:
        names = sorted(personalities)
        available = ", ".join(f"`{n}`" for n in names)
        base = f"Unknown personality: `{raw}`."
        if available:
            base += f"\n\nAvailable: `none`, {available}"
        else:
            base += "\n\nNo personalities configured."
        raise ValueError(base)

    return name, _render_personality_prompt(personalities[name])


def _apply_personality_to_session(
    sid: str, session: dict, new_prompt: str
) -> tuple[bool, dict | None]:
    if not session:
        return False, None

    try:
        info = _reset_session_agent(sid, session)
        return True, info
    except Exception:
        if session.get("agent"):
            agent = session["agent"]
            agent.ephemeral_system_prompt = new_prompt or None
            agent._cached_system_prompt = None
            info = _session_info(agent)
            _emit("session.info", sid, info)
            return False, info
        return False, None


def _background_agent_kwargs(agent, task_id: str) -> dict:
    cfg = _load_cfg()

    return {
        "base_url": getattr(agent, "base_url", None) or None,
        "api_key": getattr(agent, "api_key", None) or None,
        "provider": getattr(agent, "provider", None) or None,
        "api_mode": getattr(agent, "api_mode", None) or None,
        "acp_command": getattr(agent, "acp_command", None) or None,
        "acp_args": getattr(agent, "acp_args", None) or None,
        "model": getattr(agent, "model", None) or _resolve_model(),
        "max_iterations": int(cfg.get("max_turns", 25) or 25),
        "enabled_toolsets": getattr(agent, "enabled_toolsets", None)
        or _load_enabled_toolsets(),
        "quiet_mode": True,
        "verbose_logging": False,
        "ephemeral_system_prompt": getattr(agent, "ephemeral_system_prompt", None)
        or None,
        "providers_allowed": getattr(agent, "providers_allowed", None),
        "providers_ignored": getattr(agent, "providers_ignored", None),
        "providers_order": getattr(agent, "providers_order", None),
        "provider_sort": getattr(agent, "provider_sort", None),
        "provider_require_parameters": getattr(
            agent, "provider_require_parameters", False
        ),
        "provider_data_collection": getattr(agent, "provider_data_collection", None),
        "session_id": task_id,
        "reasoning_config": getattr(agent, "reasoning_config", None)
        or _load_reasoning_config(),
        "service_tier": getattr(agent, "service_tier", None) or _load_service_tier(),
        "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
        "platform": "tui",
        "session_db": _get_db(),
        "fallback_model": getattr(agent, "_fallback_model", None),
    }


def _reset_session_agent(sid: str, session: dict) -> dict:
    tokens = _set_session_context(session["session_key"])
    try:
        new_agent = _make_agent(
            sid, session["session_key"], session_id=session["session_key"]
        )
    finally:
        _clear_session_context(tokens)
    session["agent"] = new_agent
    session["attached_images"] = []
    session["edit_snapshots"] = {}
    session["image_counter"] = 0
    session["running"] = False
    session["show_reasoning"] = _load_show_reasoning()
    session["tool_progress_mode"] = _load_tool_progress_mode()
    session["tool_started_at"] = {}
    with session["history_lock"]:
        session["history"] = []
        session["history_version"] = int(session.get("history_version", 0)) + 1
    info = _session_info(new_agent)
    _emit("session.info", sid, info)
    _restart_slash_worker(session)
    return info


def _make_agent(sid: str, key: str, session_id: str | None = None):
    from run_agent import AIAgent
    from hermes_cli.runtime_provider import resolve_runtime_provider

    cfg = _load_cfg()
    system_prompt = cfg.get("agent", {}).get("system_prompt", "") or ""
    if not system_prompt:
        system_prompt = _resolve_personality_prompt(cfg)
    runtime = resolve_runtime_provider(requested=None)
    return AIAgent(
        model=_resolve_model(),
        provider=runtime.get("provider"),
        base_url=runtime.get("base_url"),
        api_key=runtime.get("api_key"),
        api_mode=runtime.get("api_mode"),
        acp_command=runtime.get("command"),
        acp_args=runtime.get("args"),
        credential_pool=runtime.get("credential_pool"),
        quiet_mode=True,
        verbose_logging=_load_tool_progress_mode() == "verbose",
        reasoning_config=_load_reasoning_config(),
        service_tier=_load_service_tier(),
        enabled_toolsets=_load_enabled_toolsets(),
        platform="tui",
        session_id=session_id or key,
        session_db=_get_db(),
        ephemeral_system_prompt=system_prompt or None,
        **_agent_cbs(sid),
    )


def _init_session(sid: str, key: str, agent, history: list, cols: int = 80):
    _sessions[sid] = {
        "agent": agent,
        "session_key": key,
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "image_counter": 0,
        "cols": cols,
        "slash_worker": None,
        "show_reasoning": _load_show_reasoning(),
        "tool_progress_mode": _load_tool_progress_mode(),
        "edit_snapshots": {},
        "tool_started_at": {},
    }
    try:
        _sessions[sid]["slash_worker"] = _SlashWorker(
            key, getattr(agent, "model", _resolve_model())
        )
    except Exception:
        # Defer hard-failure to slash.exec; chat still works without slash worker.
        _sessions[sid]["slash_worker"] = None
    try:
        from tools.approval import register_gateway_notify, load_permanent_allowlist

        register_gateway_notify(key, lambda data: _emit("approval.request", sid, data))
        load_permanent_allowlist()
    except Exception:
        pass
    _wire_callbacks(sid)
    _emit("session.info", sid, _session_info(agent))


def _new_session_key() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _with_checkpoints(session, fn):
    return fn(session["agent"]._checkpoint_mgr, os.getenv("TERMINAL_CWD", os.getcwd()))


def _resolve_checkpoint_hash(mgr, cwd: str, ref: str) -> str:
    try:
        checkpoints = mgr.list_checkpoints(cwd)
        idx = int(ref) - 1
    except ValueError:
        return ref
    if 0 <= idx < len(checkpoints):
        return checkpoints[idx].get("hash", ref)
    raise ValueError(f"Invalid checkpoint number. Use 1-{len(checkpoints)}.")


def _enrich_with_attached_images(user_text: str, image_paths: list[str]) -> str:
    """Pre-analyze attached images via vision and prepend descriptions to user text."""
    import asyncio, json as _json
    from tools.vision_tools import vision_analyze_tool

    prompt = (
        "Describe everything visible in this image in thorough detail. "
        "Include any text, code, data, objects, people, layout, colors, "
        "and any other notable visual information."
    )

    parts: list[str] = []
    for path in image_paths:
        p = Path(path)
        if not p.exists():
            continue
        hint = f"[You can examine it with vision_analyze using image_url: {p}]"
        try:
            r = _json.loads(
                asyncio.run(vision_analyze_tool(image_url=str(p), user_prompt=prompt))
            )
            desc = r.get("analysis", "") if r.get("success") else None
            parts.append(
                f"[The user attached an image:\n{desc}]\n{hint}"
                if desc
                else f"[The user attached an image but analysis failed.]\n{hint}"
            )
        except Exception:
            parts.append(f"[The user attached an image but analysis failed.]\n{hint}")

    text = user_text or ""
    prefix = "\n\n".join(parts)
    if prefix:
        return f"{prefix}\n\n{text}" if text else prefix
    return text or "What do you see in this image?"


def _history_to_messages(history: list[dict]) -> list[dict]:
    messages = []
    tool_call_args = {}

    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant", "tool", "system"):
            continue
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                tc_id = tc.get("id", "")
                if tc_id and fn.get("name"):
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tool_call_args[tc_id] = (fn["name"], args)
            if not (m.get("content") or "").strip():
                continue
        if role == "tool":
            tc_id = m.get("tool_call_id", "")
            tc_info = tool_call_args.get(tc_id) if tc_id else None
            name = (tc_info[0] if tc_info else None) or m.get("tool_name") or "tool"
            args = (tc_info[1] if tc_info else None) or {}
            messages.append(
                {"role": "tool", "name": name, "context": _tool_ctx(name, args)}
            )
            continue
        if not (m.get("content") or "").strip():
            continue
        messages.append({"role": role, "text": m.get("content") or ""})

    return messages


