# Auto-generated from tui_gateway/server.py — DO NOT EDIT DIRECTLY
from __future__ import annotations

from tui_gateway._state import *
from tui_gateway.handlers._core import *

# ── complete ──────────────────────────────────────────────────────────

# ── Methods: complete ─────────────────────────────────────────────────


@method("complete.path")
def _(rid, params: dict) -> dict:
    word = params.get("word", "")
    if not word:
        return _ok(rid, {"items": []})

    items: list[dict] = []
    try:
        is_context = word.startswith("@")
        query = word[1:] if is_context else word

        if is_context and not query:
            items = [
                {"text": "@diff", "display": "@diff", "meta": "git diff"},
                {"text": "@staged", "display": "@staged", "meta": "staged diff"},
                {"text": "@file:", "display": "@file:", "meta": "attach file"},
                {"text": "@folder:", "display": "@folder:", "meta": "attach folder"},
                {"text": "@url:", "display": "@url:", "meta": "fetch url"},
                {"text": "@git:", "display": "@git:", "meta": "git log"},
            ]
            return _ok(rid, {"items": items})

        # Accept both `@folder:path` and the bare `@folder` form so the user
        # sees directory listings as soon as they finish typing the keyword,
        # without first accepting the static `@folder:` hint.
        if is_context and query in ("file", "folder"):
            prefix_tag, path_part = query, ""
        elif is_context and query.startswith(("file:", "folder:")):
            prefix_tag, _, tail = query.partition(":")
            path_part = tail
        else:
            prefix_tag = ""
            path_part = query if is_context else query

        expanded = _normalize_completion_path(path_part) if path_part else "."
        if expanded == "." or not expanded:
            search_dir, match = ".", ""
        elif expanded.endswith("/"):
            search_dir, match = expanded, ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            match = os.path.basename(expanded)

        if not os.path.isdir(search_dir):
            return _ok(rid, {"items": []})

        want_dir = prefix_tag == "folder"
        match_lower = match.lower()
        for entry in sorted(os.listdir(search_dir)):
            if match and not entry.lower().startswith(match_lower):
                continue
            if is_context and not prefix_tag and entry.startswith("."):
                continue
            full = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full)
            # Explicit `@folder:` / `@file:` — honour the user's filter.  Skip
            # the opposite kind instead of auto-rewriting the completion tag,
            # which used to defeat the prefix and let `@folder:` list files.
            if prefix_tag and want_dir != is_dir:
                continue
            rel = os.path.relpath(full)
            suffix = "/" if is_dir else ""

            if is_context and prefix_tag:
                text = f"@{prefix_tag}:{rel}{suffix}"
            elif is_context:
                kind = "folder" if is_dir else "file"
                text = f"@{kind}:{rel}{suffix}"
            elif word.startswith("~"):
                text = "~/" + os.path.relpath(full, os.path.expanduser("~")) + suffix
            elif word.startswith("./"):
                text = "./" + rel + suffix
            else:
                text = rel + suffix

            items.append(
                {
                    "text": text,
                    "display": entry + suffix,
                    "meta": "dir" if is_dir else "",
                }
            )
            if len(items) >= 30:
                break
    except Exception as e:
        return _err(rid, 5021, str(e))

    return _ok(rid, {"items": items})


@method("complete.slash")
def _(rid, params: dict) -> dict:
    text = params.get("text", "")
    if not text.startswith("/"):
        return _ok(rid, {"items": []})

    try:
        from hermes_cli.commands import SlashCommandCompleter
        from prompt_toolkit.document import Document
        from prompt_toolkit.formatted_text import to_plain_text

        from agent.skill_commands import get_skill_commands

        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: get_skill_commands()
        )
        doc = Document(text, len(text))
        items = [
            {
                "text": c.text,
                "display": c.display or c.text,
                "meta": to_plain_text(c.display_meta) if c.display_meta else "",
            }
            for c in completer.get_completions(doc, None)
        ][:30]
        text_lower = text.lower()
        extras = [
            {
                "text": "/compact",
                "display": "/compact",
                "meta": "Toggle compact display mode",
            },
            {
                "text": "/logs",
                "display": "/logs",
                "meta": "Show recent gateway log lines",
            },
        ]
        for extra in extras:
            if extra["text"].startswith(text_lower) and not any(
                item["text"] == extra["text"] for item in items
            ):
                items.append(extra)
        return _ok(
            rid,
            {"items": items, "replace_from": text.rfind(" ") + 1 if " " in text else 1},
        )
    except Exception as e:
        return _err(rid, 5020, str(e))


@method("model.options")
def _(rid, params: dict) -> dict:
    try:
        from hermes_cli.model_switch import list_authenticated_providers

        session = _sessions.get(params.get("session_id", ""))
        agent = session.get("agent") if session else None
        cfg = _load_cfg()
        current_provider = getattr(agent, "provider", "") or ""
        current_model = getattr(agent, "model", "") or _resolve_model()
        # list_authenticated_providers already populates each provider's
        # "models" with the curated list (same source as `hermes model` and
        # classic CLI's /model picker). Do NOT overwrite with live
        # provider_model_ids() — that bypasses curation and pulls in
        # non-agentic models (e.g. Nous /models returns ~400 IDs including
        # TTS, embeddings, rerankers, image/video generators).
        providers = list_authenticated_providers(
            current_provider=current_provider,
            user_providers=(
                cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
            ),
            custom_providers=(
                cfg.get("custom_providers")
                if isinstance(cfg.get("custom_providers"), list)
                else []
            ),
            max_models=50,
        )
        return _ok(
            rid,
            {
                "providers": providers,
                "model": current_model,
                "provider": current_provider,
            },
        )
    except Exception as e:
        return _err(rid, 5033, str(e))


