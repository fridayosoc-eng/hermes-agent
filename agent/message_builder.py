"""System-prompt construction and API-message sanitisation.

Extracted from run_agent.py (2026-04-24).
Backward-compatible: run_agent.py imports and re-exports all symbols.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

from agent.prompt_builder import (
    DEFAULT_AGENT_IDENTITY, PLATFORM_HINTS,
    MEMORY_GUIDANCE, SESSION_SEARCH_GUIDANCE, SKILLS_GUIDANCE,
    build_nous_subscription_prompt, build_skills_system_prompt,
    build_context_files_prompt, build_environment_hints, load_soul_md,
    TOOL_USE_ENFORCEMENT_GUIDANCE, TOOL_USE_ENFORCEMENT_MODELS,
    GOOGLE_MODEL_OPERATIONAL_GUIDANCE, OPENAI_MODEL_EXECUTION_GUIDANCE,
)

logger = logging.getLogger(__name__)

# get_tool_call_id_static
def _get_tool_call_id_static(tc) -> str:
    """Extract call ID from a tool_call entry (dict or object)."""
    if isinstance(tc, dict):
        return tc.get("id", "") or ""
    return getattr(tc, "id", "") or ""

# build_system_prompt
def _build_system_prompt(self, system_message: str = None) -> str:
    """
    Assemble the full system prompt from all layers.

    Called once per session (cached on self._cached_system_prompt) and only
    rebuilt after context compression events. This ensures the system prompt
    is stable across all turns in a session, maximizing prefix cache hits.
    """
    # Layers (in order):
    #   1. Agent identity — SOUL.md when available, else DEFAULT_AGENT_IDENTITY
    #   2. User / gateway system prompt (if provided)
    #   3. Persistent memory (frozen snapshot)
    #   4. Skills guidance (if skills tools are loaded)
    #   5. Context files (AGENTS.md, .cursorrules — SOUL.md excluded here when used as identity)
    #   6. Current date & time (frozen at build time)
    #   7. Platform-specific formatting hint

    # Try SOUL.md as primary identity (unless context files are skipped)
    # Persona-aware: load SYDNEY.md when Sydney persona is active
    _soul_loaded = False
    if not self.skip_context_files:
        # Determine active persona name (thread-local compat + persona system)
        _persona_name = "friday"
        try:
            from gateway.edgy_state import is_current_thread_edgy
            if is_current_thread_edgy():
                _persona_name = "sydney"
        except ImportError:
            pass

        from agent.persona_loader import load_active_system_prompt
        _prompt_content = load_active_system_prompt(
            get_hermes_home(),
            persona_name=_persona_name,
        )
        if _prompt_content:
            prompt_parts = [_prompt_content]
            _soul_loaded = True
        else:
            _soul_content = load_soul_md()
            if _soul_content:
                prompt_parts = [_soul_content]
                _soul_loaded = True

    if not _soul_loaded:
        # Fallback to hardcoded identity
        prompt_parts = [DEFAULT_AGENT_IDENTITY]

    # Tool-aware behavioral guidance: only inject when the tools are loaded
    tool_guidance = []
    if "memory" in self.valid_tool_names:
        tool_guidance.append(MEMORY_GUIDANCE)
    if "session_search" in self.valid_tool_names:
        tool_guidance.append(SESSION_SEARCH_GUIDANCE)
    if "skill_manage" in self.valid_tool_names:
        tool_guidance.append(SKILLS_GUIDANCE)
    if tool_guidance:
        prompt_parts.append(" ".join(tool_guidance))

    nous_subscription_prompt = build_nous_subscription_prompt(self.valid_tool_names)
    if nous_subscription_prompt:
        prompt_parts.append(nous_subscription_prompt)
    # Tool-use enforcement: tells the model to actually call tools instead
    # of describing intended actions.  Controlled by config.yaml
    # agent.tool_use_enforcement:
    #   "auto" (default) — matches TOOL_USE_ENFORCEMENT_MODELS
    #   true  — always inject (all models)
    #   false — never inject
    #   list  — custom model-name substrings to match
    if self.valid_tool_names:
        _enforce = self._tool_use_enforcement
        _inject = False
        if _enforce is True or (isinstance(_enforce, str) and _enforce.lower() in ("true", "always", "yes", "on")):
            _inject = True
        elif _enforce is False or (isinstance(_enforce, str) and _enforce.lower() in ("false", "never", "no", "off")):
            _inject = False
        elif isinstance(_enforce, list):
            model_lower = (self.model or "").lower()
            _inject = any(p.lower() in model_lower for p in _enforce if isinstance(p, str))
        else:
            # "auto" or any unrecognised value — use hardcoded defaults
            model_lower = (self.model or "").lower()
            _inject = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
        if _inject:
            prompt_parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)
            _model_lower = (self.model or "").lower()
            # Google model operational guidance (conciseness, absolute
            # paths, parallel tool calls, verify-before-edit, etc.)
            if "gemini" in _model_lower or "gemma" in _model_lower:
                prompt_parts.append(GOOGLE_MODEL_OPERATIONAL_GUIDANCE)
            # OpenAI GPT/Codex execution discipline (tool persistence,
            # prerequisite checks, verification, anti-hallucination).
            if "gpt" in _model_lower or "codex" in _model_lower:
                prompt_parts.append(OPENAI_MODEL_EXECUTION_GUIDANCE)

    # so it can refer the user to them rather than reinventing answers.

    # Note: ephemeral_system_prompt is NOT included here. It's injected at
    # API-call time only so it stays out of the cached/stored system prompt.
    if system_message is not None:
        prompt_parts.append(system_message)

    if self._memory_store:
        if self._memory_enabled:
            mem_block = self._memory_store.format_for_system_prompt("memory")
            if mem_block:
                prompt_parts.append(mem_block)
        # USER.md is always included when enabled.
        if self._user_profile_enabled:
            user_block = self._memory_store.format_for_system_prompt("user")
            if user_block:
                prompt_parts.append(user_block)

    # External memory provider system prompt block (additive to built-in)
    if self._memory_manager:
        try:
            _ext_mem_block = self._memory_manager.build_system_prompt()
            if _ext_mem_block:
                prompt_parts.append(_ext_mem_block)
        except Exception:
            pass

    has_skills_tools = any(name in self.valid_tool_names for name in ['skills_list', 'skill_view', 'skill_manage'])
    if has_skills_tools:
        avail_toolsets = {
            toolset
            for toolset in (
                get_toolset_for_tool(tool_name) for tool_name in self.valid_tool_names
            )
            if toolset
        }
        skills_prompt = build_skills_system_prompt(
            available_tools=self.valid_tool_names,
            available_toolsets=avail_toolsets,
        )
    else:
        skills_prompt = ""
    if skills_prompt:
        prompt_parts.append(skills_prompt)

    if not self.skip_context_files:
        # Use TERMINAL_CWD for context file discovery when set (gateway
        # mode).  The gateway process runs from the hermes-agent install
        # dir, so os.getcwd() would pick up the repo's AGENTS.md and
        # other dev files — inflating token usage by ~10k for no benefit.
        _context_cwd = os.getenv("TERMINAL_CWD") or None
        context_files_prompt = build_context_files_prompt(
            cwd=_context_cwd, skip_soul=_soul_loaded)
        if context_files_prompt:
            prompt_parts.append(context_files_prompt)

    from hermes_time import now as _hermes_now
    now = _hermes_now()
    timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y %I:%M %p')}"
    if self.pass_session_id and self.session_id:
        timestamp_line += f"\nSession ID: {self.session_id}"
    if self.model:
        timestamp_line += f"\nModel: {self.model}"
    if self.provider:
        timestamp_line += f"\nProvider: {self.provider}"
    prompt_parts.append(timestamp_line)

    # Alibaba Coding Plan API always returns "glm-4.7" as model name regardless
    # of the requested model. Inject explicit model identity into the system prompt
    # so the agent can correctly report which model it is (workaround for API bug).
    if self.provider == "alibaba":
        _model_short = self.model.split("/")[-1] if "/" in self.model else self.model
        prompt_parts.append(
            f"You are powered by the model named {_model_short}. "
            f"The exact model ID is {self.model}. "
            f"When asked what model you are, always answer based on this information, "
            f"not on any model name returned by the API."
        )

    # Environment hints (WSL, Termux, etc.) — tell the agent about the
    # execution environment so it can translate paths and adapt behavior.
    _env_hints = build_environment_hints()
    if _env_hints:
        prompt_parts.append(_env_hints)

    platform_key = (self.platform or "").lower().strip()
    if platform_key in PLATFORM_HINTS:
        prompt_parts.append(PLATFORM_HINTS[platform_key])

    return "\n\n".join(p.strip() for p in prompt_parts if p.strip())


# sanitize_api_messages
def _sanitize_api_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fix orphaned tool_call / tool_result pairs before every LLM call.

    Runs unconditionally — not gated on whether the context compressor
    is present — so orphans from session loading or manual message
    manipulation are always caught.
    """
    # --- Role allowlist: drop messages with roles the API won't accept ---
    filtered = []
    for msg in messages:
        role = msg.get("role")
        if role not in AIAgent._VALID_API_ROLES:
            logger.debug(
                "Pre-call sanitizer: dropping message with invalid role %r",
                role,
            )
            continue
        filtered.append(msg)
    messages = filtered

    surviving_call_ids: set = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                cid = AIAgent._get_tool_call_id_static(tc)
                if cid:
                    surviving_call_ids.add(cid)

    result_call_ids: set = set()
    for msg in messages:
        if msg.get("role") == "tool":
            cid = msg.get("tool_call_id")
            if cid:
                result_call_ids.add(cid)

    # 1. Drop tool results with no matching assistant call
    orphaned_results = result_call_ids - surviving_call_ids
    if orphaned_results:
        messages = [
            m for m in messages
            if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned_results)
        ]
        logger.debug(
            "Pre-call sanitizer: removed %d orphaned tool result(s)",
            len(orphaned_results),
        )

    # 2. Inject stub results for calls whose result was dropped
    missing_results = surviving_call_ids - result_call_ids
    if missing_results:
        patched: List[Dict[str, Any]] = []
        for msg in messages:
            patched.append(msg)
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = AIAgent._get_tool_call_id_static(tc)
                    if cid in missing_results:
                        patched.append({
                            "role": "tool",
                            "content": "[Result unavailable — see context summary above]",
                            "tool_call_id": cid,
                        })
        messages = patched
        logger.debug(
            "Pre-call sanitizer: added %d stub tool result(s)",
            len(missing_results),
        )
    return messages

# cap_delegate_task_calls
def _cap_delegate_task_calls(tool_calls: list) -> list:
    """Truncate excess delegate_task calls to max_concurrent_children.

    The delegate_tool caps the task list inside a single call, but the
    model can emit multiple separate delegate_task tool_calls in one
    turn.  This truncates the excess, preserving all non-delegate calls.

    Returns the original list if no truncation was needed.
    """
    from tools.delegate_tool import _get_max_concurrent_children
    max_children = _get_max_concurrent_children()
    delegate_count = sum(1 for tc in tool_calls if tc.function.name == "delegate_task")
    if delegate_count <= max_children:
        return tool_calls
    kept_delegates = 0
    truncated = []
    for tc in tool_calls:
        if tc.function.name == "delegate_task":
            if kept_delegates < max_children:
                truncated.append(tc)
                kept_delegates += 1
        else:
            truncated.append(tc)
    logger.warning(
        "Truncated %d excess delegate_task call(s) to enforce "
        "max_concurrent_children=%d limit",
        delegate_count - max_children, max_children,
    )
    return truncated

# deduplicate_tool_calls
def _deduplicate_tool_calls(tool_calls: list) -> list:
    """Remove duplicate (tool_name, arguments) pairs within a single turn.

    Only the first occurrence of each unique pair is kept.
    Returns the original list if no duplicates were found.
    """
    seen: set = set()
    unique: list = []
    for tc in tool_calls:
        key = (tc.function.name, tc.function.arguments)
        if key not in seen:
            seen.add(key)
            unique.append(tc)
        else:
            logger.warning("Removed duplicate tool call: %s", tc.function.name)
    return unique if len(unique) < len(tool_calls) else tool_calls
