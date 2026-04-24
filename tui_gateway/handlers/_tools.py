# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── tools ──────────────────────────────────────────────────────────

# ── Methods: tools & system ──────────────────────────────────────────


@method("process.stop")
def _(rid, params: dict) -> dict:
    try:
        from tools.process_registry import process_registry

        return _ok(rid, {"killed": process_registry.kill_all()})
    except Exception as e:
        return _err(rid, 5010, str(e))


@method("reload.mcp")
def _(rid, params: dict) -> dict:
    session = _sessions.get(params.get("session_id", ""))
    try:
        from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools

        shutdown_mcp_servers()
        discover_mcp_tools()
        if session:
            agent = session["agent"]
            if hasattr(agent, "refresh_tools"):
                agent.refresh_tools()
            _emit("session.info", params.get("session_id", ""), _session_info(agent))
        return _ok(rid, {"status": "reloaded"})
    except Exception as e:
        return _err(rid, 5015, str(e))


_TUI_HIDDEN: frozenset[str] = frozenset(
    {
        "sethome",
        "set-home",
        "update",
        "commands",
        "status",
        "approve",
        "deny",
    }
)

_TUI_EXTRA: list[tuple[str, str, str]] = [
    ("/compact", "Toggle compact display mode", "TUI"),
    ("/logs", "Show recent gateway log lines", "TUI"),
]

# Commands that queue messages onto _pending_input in the CLI.
# In the TUI the slash worker subprocess has no reader for that queue,
# so slash.exec rejects them → TUI falls through to command.dispatch.
_PENDING_INPUT_COMMANDS: frozenset[str] = frozenset(
    {
        "retry",
        "queue",
        "q",
        "steer",
        "plan",
    }
)


@method("commands.catalog")
def _(rid, params: dict) -> dict:
    """Registry-backed slash metadata for the TUI — categorized, no aliases."""
    try:
        from hermes_cli.commands import (
            COMMAND_REGISTRY,
            SUBCOMMANDS,
            _build_description,
        )

        all_pairs: list[list[str]] = []
        canon: dict[str, str] = {}
        categories: list[dict] = []
        cat_map: dict[str, list[list[str]]] = {}
        cat_order: list[str] = []

        for cmd in COMMAND_REGISTRY:
            c = f"/{cmd.name}"
            canon[c.lower()] = c
            for a in cmd.aliases:
                canon[f"/{a}".lower()] = c

            if cmd.name in _TUI_HIDDEN:
                continue

            desc = _build_description(cmd)
            all_pairs.append([c, desc])

            cat = cmd.category
            if cat not in cat_map:
                cat_map[cat] = []
                cat_order.append(cat)
            cat_map[cat].append([c, desc])

        for name, desc, cat in _TUI_EXTRA:
            all_pairs.append([name, desc])
            if cat not in cat_map:
                cat_map[cat] = []
                cat_order.append(cat)
            cat_map[cat].append([name, desc])

        warning = ""
        try:
            qcmds = _load_cfg().get("quick_commands", {}) or {}
            if isinstance(qcmds, dict) and qcmds:
                bucket = "User commands"
                if bucket not in cat_map:
                    cat_map[bucket] = []
                    cat_order.append(bucket)
                for qname, qc in sorted(qcmds.items()):
                    if not isinstance(qc, dict):
                        continue
                    key = f"/{qname}"
                    canon[key.lower()] = key
                    qtype = qc.get("type", "")
                    if qtype == "exec":
                        default_desc = f"exec: {qc.get('command', '')}"
                    elif qtype == "alias":
                        default_desc = f"alias → {qc.get('target', '')}"
                    else:
                        default_desc = qtype or "quick command"
                    qdesc = str(qc.get("description") or default_desc)
                    qdesc = qdesc[:120] + ("…" if len(qdesc) > 120 else "")
                    all_pairs.append([key, qdesc])
                    cat_map[bucket].append([key, qdesc])
        except Exception as e:
            if not warning:
                warning = f"quick_commands discovery unavailable: {e}"

        skill_count = 0
        try:
            from agent.skill_commands import scan_skill_commands

            for k, info in sorted(scan_skill_commands().items()):
                d = str(info.get("description", "Skill"))
                all_pairs.append([k, d[:120] + ("…" if len(d) > 120 else "")])
                skill_count += 1
        except Exception as e:
            warning = f"skill discovery unavailable: {e}"

        for cat in cat_order:
            categories.append({"name": cat, "pairs": cat_map[cat]})

        sub = {k: v[:] for k, v in SUBCOMMANDS.items()}
        return _ok(
            rid,
            {
                "pairs": all_pairs,
                "sub": sub,
                "canon": canon,
                "categories": categories,
                "skill_count": skill_count,
                "warning": warning,
            },
        )
    except Exception as e:
        return _err(rid, 5020, str(e))


def _cli_exec_blocked(argv: list[str]) -> str | None:
    """Return user hint if this argv must not run headless in the gateway process."""
    if not argv:
        return "bare `hermes` is interactive — use `/hermes chat -q …` or run `hermes` in another terminal"
    a0 = argv[0].lower()
    if a0 == "setup":
        return "`hermes setup` needs a full terminal — run it outside the TUI"
    if a0 == "gateway":
        return "`hermes gateway` is long-running — run it in another terminal"
    if a0 == "sessions" and len(argv) > 1 and argv[1].lower() == "browse":
        return "`hermes sessions browse` is interactive — use /resume here, or run browse in another terminal"
    if a0 == "config" and len(argv) > 1 and argv[1].lower() == "edit":
        return "`hermes config edit` needs $EDITOR in a real terminal"
    return None


@method("cli.exec")
def _(rid, params: dict) -> dict:
    """Run `python -m hermes_cli.main` with argv; capture stdout/stderr (non-interactive only)."""
    argv = params.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        return _err(rid, 4003, "argv must be list[str]")
    hint = _cli_exec_blocked(argv)
    if hint:
        return _ok(rid, {"blocked": True, "hint": hint, "code": -1, "output": ""})
    try:
        r = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", *argv],
            capture_output=True,
            text=True,
            timeout=min(int(params.get("timeout", 240)), 600),
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        parts = [r.stdout or "", r.stderr or ""]
        out = "\n".join(p for p in parts if p).strip() or "(no output)"
        return _ok(
            rid, {"blocked": False, "code": r.returncode, "output": out[:48_000]}
        )
    except subprocess.TimeoutExpired:
        return _err(rid, 5016, "cli.exec: timeout")
    except Exception as e:
        return _err(rid, 5017, str(e))


@method("command.resolve")
def _(rid, params: dict) -> dict:
    try:
        from hermes_cli.commands import resolve_command

        r = resolve_command(params.get("name", ""))
        if r:
            return _ok(
                rid,
                {
                    "canonical": r.name,
                    "description": r.description,
                    "category": r.category,
                },
            )
        return _err(rid, 4011, f"unknown command: {params.get('name')}")
    except Exception as e:
        return _err(rid, 5012, str(e))


def _resolve_name(name: str) -> str:
    try:
        from hermes_cli.commands import resolve_command

        r = resolve_command(name)
        return r.name if r else name
    except Exception:
        return name


@method("command.dispatch")
def _(rid, params: dict) -> dict:
    name, arg = params.get("name", "").lstrip("/"), params.get("arg", "")
    resolved = _resolve_name(name)
    if resolved != name:
        name = resolved
    session = _sessions.get(params.get("session_id", ""))

    qcmds = _load_cfg().get("quick_commands", {})
    if name in qcmds:
        qc = qcmds[name]
        if qc.get("type") == "exec":
            r = subprocess.run(
                qc.get("command", ""),
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (
                (r.stdout or "")
                + ("\n" if r.stdout and r.stderr else "")
                + (r.stderr or "")
            ).strip()[:4000]
            if r.returncode != 0:
                return _err(
                    rid,
                    4018,
                    output or f"quick command failed with exit code {r.returncode}",
                )
            return _ok(rid, {"type": "exec", "output": output})
        if qc.get("type") == "alias":
            return _ok(rid, {"type": "alias", "target": qc.get("target", "")})

    try:
        from hermes_cli.plugins import get_plugin_command_handler

        handler = get_plugin_command_handler(name)
        if handler:
            return _ok(rid, {"type": "plugin", "output": str(handler(arg) or "")})
    except Exception:
        pass

    try:
        from agent.skill_commands import (
            scan_skill_commands,
            build_skill_invocation_message,
        )

        cmds = scan_skill_commands()
        key = f"/{name}"
        if key in cmds:
            msg = build_skill_invocation_message(
                key, arg, task_id=session.get("session_key", "") if session else ""
            )
            if msg:
                return _ok(
                    rid,
                    {
                        "type": "skill",
                        "message": msg,
                        "name": cmds[key].get("name", name),
                    },
                )
    except Exception:
        pass

    # ── Commands that queue messages onto _pending_input in the CLI ───
    # In the TUI the slash worker subprocess has no reader for that queue,
    # so we handle them here and return a structured payload.

    if name in ("queue", "q"):
        if not arg:
            return _err(rid, 4004, "usage: /queue <prompt>")
        return _ok(rid, {"type": "send", "message": arg})

    if name == "retry":
        if not session:
            return _err(rid, 4001, "no active session to retry")
        if session.get("running"):
            return _err(
                rid, 4009, "session busy — /interrupt the current turn before /retry"
            )
        history = session.get("history", [])
        if not history:
            return _err(rid, 4018, "no previous user message to retry")
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return _err(rid, 4018, "no previous user message to retry")
        content = history[last_user_idx].get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if not content:
            return _err(rid, 4018, "last user message is empty")
        # Truncate history: remove everything from the last user message onward
        # (mirrors CLI retry_last() which strips the failed exchange)
        with session["history_lock"]:
            session["history"] = history[:last_user_idx]
            session["history_version"] = int(session.get("history_version", 0)) + 1
        return _ok(rid, {"type": "send", "message": content})

    if name == "steer":
        if not arg:
            return _err(rid, 4004, "usage: /steer <prompt>")
        agent = session.get("agent") if session else None
        if agent and hasattr(agent, "steer"):
            try:
                accepted = agent.steer(arg)
                if accepted:
                    return _ok(
                        rid,
                        {
                            "type": "exec",
                            "output": f"⏩ Steer queued — arrives after the next tool call: {arg[:80]}{'...' if len(arg) > 80 else ''}",
                        },
                    )
            except Exception:
                pass
        # Fallback: no active run, treat as next-turn message
        return _ok(rid, {"type": "send", "message": arg})

    if name == "plan":
        try:
            from agent.skill_commands import (
                build_skill_invocation_message as _bsim,
                build_plan_path,
            )

            user_instruction = arg or ""
            plan_path = build_plan_path(user_instruction)
            msg = _bsim(
                "/plan",
                user_instruction,
                task_id=session.get("session_key", "") if session else "",
                runtime_note=(
                    "Save the markdown plan with write_file to this exact relative path "
                    f"inside the active workspace/backend cwd: {plan_path}"
                ),
            )
            if msg:
                return _ok(rid, {"type": "send", "message": msg})
        except Exception as e:
            return _err(rid, 5030, f"plan skill failed: {e}")

    return _err(rid, 4018, f"not a quick/plugin/skill command: {name}")


