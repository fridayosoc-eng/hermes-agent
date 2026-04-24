# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── session ──────────────────────────────────────────────────────────

# ── Methods: session ─────────────────────────────────────────────────


@method("session.create")
def _(rid, params: dict) -> dict:
    sid = uuid.uuid4().hex[:8]
    key = _new_session_key()
    cols = int(params.get("cols", 80))
    _enable_gateway_prompts()

    ready = threading.Event()

    _sessions[sid] = {
        "agent": None,
        "agent_error": None,
        "agent_ready": ready,
        "attached_images": [],
        "cols": cols,
        "edit_snapshots": {},
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "image_counter": 0,
        "running": False,
        "session_key": key,
        "show_reasoning": _load_show_reasoning(),
        "slash_worker": None,
        "tool_progress_mode": _load_tool_progress_mode(),
        "tool_started_at": {},
    }

    def _build() -> None:
        session = _sessions.get(sid)
        if session is None:
            # session.close ran before the build thread got scheduled.
            ready.set()
            return

        # Track what we allocate so we can clean up if session.close
        # races us to the finish line.  session.close pops _sessions[sid]
        # unconditionally and tries to close the slash_worker it finds;
        # if _build is still mid-construction when close runs, close
        # finds slash_worker=None / notify unregistered and returns
        # cleanly — leaving us, the build thread, to later install the
        # worker + notify on an orphaned session dict.  The finally
        # block below detects the orphan and cleans up instead of
        # leaking a subprocess and a global notify registration.
        worker = None
        notify_registered = False
        try:
            tokens = _set_session_context(key)
            try:
                agent = _make_agent(sid, key)
            finally:
                _clear_session_context(tokens)

            db = _get_db()
            if db is not None:
                db.create_session(key, source="tui", model=_resolve_model())
            session["agent"] = agent

            try:
                worker = _SlashWorker(key, getattr(agent, "model", _resolve_model()))
                session["slash_worker"] = worker
            except Exception:
                pass

            try:
                from tools.approval import (
                    register_gateway_notify,
                    load_permanent_allowlist,
                )

                register_gateway_notify(
                    key, lambda data: _emit("approval.request", sid, data)
                )
                notify_registered = True
                load_permanent_allowlist()
            except Exception:
                pass

            _wire_callbacks(sid)

            info = _session_info(agent)
            warn = _probe_credentials(agent)
            if warn:
                info["credential_warning"] = warn
            _emit("session.info", sid, info)
        except Exception as e:
            session["agent_error"] = str(e)
            _emit("error", sid, {"message": f"agent init failed: {e}"})
        finally:
            # Orphan check: if session.close raced us and popped
            # _sessions[sid] while we were building, the dict we just
            # populated is unreachable.  Clean up the subprocess and
            # the global notify registration ourselves — session.close
            # couldn't see them at the time it ran.
            if _sessions.get(sid) is not session:
                if worker is not None:
                    try:
                        worker.close()
                    except Exception:
                        pass
                if notify_registered:
                    try:
                        from tools.approval import unregister_gateway_notify

                        unregister_gateway_notify(key)
                    except Exception:
                        pass
            ready.set()

    threading.Thread(target=_build, daemon=True).start()

    return _ok(
        rid,
        {
            "session_id": sid,
            "info": {
                "model": _resolve_model(),
                "tools": {},
                "skills": {},
                "cwd": os.getenv("TERMINAL_CWD", os.getcwd()),
            },
        },
    )


@method("session.list")
def _(rid, params: dict) -> dict:
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5006)
    try:
        # Resume picker should include human conversation surfaces beyond
        # tui/cli (notably telegram from blitz row #7), but avoid internal
        # sources that clutter the modal (tool/acp/etc).
        allow = frozenset(
            {
                "cli",
                "tui",
                "telegram",
                "discord",
                "slack",
                "whatsapp",
                "wecom",
                "weixin",
                "feishu",
                "signal",
                "mattermost",
                "matrix",
                "qq",
            }
        )

        limit = int(params.get("limit", 20) or 20)
        fetch_limit = max(limit * 5, 100)
        rows = [
            s
            for s in db.list_sessions_rich(source=None, limit=fetch_limit)
            if (s.get("source") or "").strip().lower() in allow
        ][:limit]
        return _ok(
            rid,
            {
                "sessions": [
                    {
                        "id": s["id"],
                        "title": s.get("title") or "",
                        "preview": s.get("preview") or "",
                        "started_at": s.get("started_at") or 0,
                        "message_count": s.get("message_count") or 0,
                        "source": s.get("source") or "",
                    }
                    for s in rows
                ]
            },
        )
    except Exception as e:
        return _err(rid, 5006, str(e))


@method("session.resume")
def _(rid, params: dict) -> dict:
    target = params.get("session_id", "")
    if not target:
        return _err(rid, 4006, "session_id required")
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5000)
    found = db.get_session(target)
    if not found:
        found = db.get_session_by_title(target)
        if found:
            target = found["id"]
        else:
            return _err(rid, 4007, "session not found")
    sid = uuid.uuid4().hex[:8]
    _enable_gateway_prompts()
    try:
        db.reopen_session(target)
        history = db.get_messages_as_conversation(target)
        messages = _history_to_messages(history)
        tokens = _set_session_context(target)
        try:
            agent = _make_agent(sid, target, session_id=target)
        finally:
            _clear_session_context(tokens)
        _init_session(sid, target, agent, history, cols=int(params.get("cols", 80)))
    except Exception as e:
        return _err(rid, 5000, f"resume failed: {e}")
    return _ok(
        rid,
        {
            "session_id": sid,
            "resumed": target,
            "message_count": len(messages),
            "messages": messages,
            "info": _session_info(agent),
        },
    )


@method("session.title")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5007)
    title, key = params.get("title", ""), session["session_key"]
    if not title:
        return _ok(
            rid, {"title": db.get_session_title(key) or "", "session_key": key}
        )
    try:
        db.set_session_title(key, title)
        return _ok(rid, {"title": title})
    except Exception as e:
        return _err(rid, 5007, str(e))


@method("session.usage")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    return err or _ok(rid, _get_usage(session["agent"]))


@method("session.history")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    return err or _ok(
        rid,
        {
            "count": len(session.get("history", [])),
            "messages": _history_to_messages(list(session.get("history", []))),
        },
    )


@method("session.undo")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    # Reject during an in-flight turn.  If we mutated history while
    # the agent thread is running, prompt.submit's post-run history
    # write would either clobber the undo (version matches) or
    # silently drop the agent's output (version mismatch, see below).
    # Neither is what the user wants — make them /interrupt first.
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /undo"
        )
    removed = 0
    with session["history_lock"]:
        history = session.get("history", [])
        while history and history[-1].get("role") in ("assistant", "tool"):
            history.pop()
            removed += 1
        if history and history[-1].get("role") == "user":
            history.pop()
            removed += 1
        if removed:
            session["history_version"] = int(session.get("history_version", 0)) + 1
    return _ok(rid, {"removed": removed})


@method("session.compress")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /compress"
        )
    try:
        with session["history_lock"]:
            removed, usage = _compress_session_history(
                session, str(params.get("focus_topic", "") or "").strip()
            )
            messages = list(session.get("history", []))
        info = _session_info(session["agent"])
        _emit("session.info", params.get("session_id", ""), info)
        return _ok(
            rid,
            {
                "status": "compressed",
                "removed": removed,
                "usage": usage,
                "info": info,
                "messages": messages,
            },
        )
    except Exception as e:
        return _err(rid, 5005, str(e))


@method("session.save")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    import time as _time

    filename = os.path.abspath(
        f"hermes_conversation_{_time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    try:
        with open(filename, "w") as f:
            json.dump(
                {
                    "model": getattr(session["agent"], "model", ""),
                    "messages": session.get("history", []),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return _ok(rid, {"file": filename})
    except Exception as e:
        return _err(rid, 5011, str(e))


@method("session.close")
def _(rid, params: dict) -> dict:
    sid = params.get("session_id", "")
    session = _sessions.pop(sid, None)
    if not session:
        return _ok(rid, {"closed": False})
    try:
        from tools.approval import unregister_gateway_notify

        unregister_gateway_notify(session["session_key"])
    except Exception:
        pass
    try:
        worker = session.get("slash_worker")
        if worker:
            worker.close()
    except Exception:
        pass
    return _ok(rid, {"closed": True})


@method("session.branch")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5008)
    old_key = session["session_key"]
    with session["history_lock"]:
        history = [dict(msg) for msg in session.get("history", [])]
    if not history:
        return _err(rid, 4008, "nothing to branch — send a message first")
    new_key = _new_session_key()
    branch_name = params.get("name", "")
    try:
        if branch_name:
            title = branch_name
        else:
            current = db.get_session_title(old_key) or "branch"
            title = (
                db.get_next_title_in_lineage(current)
                if hasattr(db, "get_next_title_in_lineage")
                else f"{current} (branch)"
            )
        db.create_session(
            new_key, source="tui", model=_resolve_model(), parent_session_id=old_key
        )
        for msg in history:
            db.append_message(
                session_id=new_key,
                role=msg.get("role", "user"),
                content=msg.get("content"),
            )
        db.set_session_title(new_key, title)
    except Exception as e:
        return _err(rid, 5008, f"branch failed: {e}")
    new_sid = uuid.uuid4().hex[:8]
    try:
        tokens = _set_session_context(new_key)
        try:
            agent = _make_agent(new_sid, new_key, session_id=new_key)
        finally:
            _clear_session_context(tokens)
        _init_session(
            new_sid, new_key, agent, list(history), cols=session.get("cols", 80)
        )
    except Exception as e:
        return _err(rid, 5000, f"agent init failed on branch: {e}")
    return _ok(rid, {"session_id": new_sid, "title": title, "parent": old_key})


@method("session.interrupt")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    if hasattr(session["agent"], "interrupt"):
        session["agent"].interrupt()
    # Scope the pending-prompt release to THIS session.  A global
    # _clear_pending() would collaterally cancel clarify/sudo/secret
    # prompts on unrelated sessions sharing the same tui_gateway
    # process, silently resolving them to empty strings.
    _clear_pending(params.get("session_id", ""))
    try:
        from tools.approval import resolve_gateway_approval

        resolve_gateway_approval(session["session_key"], "deny", resolve_all=True)
    except Exception:
        pass
    return _ok(rid, {"status": "interrupted"})


