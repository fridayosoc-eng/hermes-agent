# Hermes-Agent Implementation Plan

**Date:** 2026-04-26
**Related:** AUDIT-2026-04-26.md
**Branch:** johnny (implementation on feature branch)
**Philosophy:** Incremental phases. Each phase is independently deployable and testable. EDGY mode changes are especially careful — the persona system is core value.

---

## Pre-Flight: Create Feature Branch

```bash
cd /Users/fridayos/.hermes/hermes-agent
git checkout -b audit/hardening-and-personas
```

All work below is committed incrementally to this branch. Each phase ends with a test run and a checkpoint commit.

---

## Phase 1: Critical Quick Wins (Low Risk, High Impact)

**Goal:** Fix the most dangerous issues without touching any architecture.

### 1.1 Fix File Descriptor Leaks

**C5 — `tools/environments/local.py:393`**
```python
# BEFORE:
cwd_path = open(self._cwd_file).read().strip()

# AFTER:
with open(self._cwd_file) as f:
    cwd_path = f.read().strip()
```

**C6 — `tools/mcp_tool.py:131,138`**
- Add `atexit.register` for `_mcp_stderr_log_fh`
- Or wrap in a cleanup function called from gateway shutdown

**H9 — `tools/tts_tool.py:1049-1061`**
- After successful ffmpeg conversion, delete source file:
```python
# After ffmpeg succeeds:
os.remove(mp3_path)  # clean up pre-conversion file
```

**H10 — `tools/rl_training_tool.py:336,359,400`**
- Change all 3 `open()` to `with open(...)` pattern

### 1.2 Fix Security: API Key Logging

**C1 — `run_agent.py:1139`**
```python
# BEFORE:
print(f"Using token: {effective_key[:8]}...{effective_key[-4:]}")

# AFTER:
logger.debug("Using API key: ***...***")
```

**H2 — `hermes_cli/webhook.py:178`**
```python
# BEFORE:
print(f"  Secret: {secret}")

# AFTER:
print("  Secret: ********")
```

**H3 — `hermes_cli/dingtalk_auth.py:292`**
```python
# BEFORE:
f"  Client Secret: {client_secret[:8]}{'*' * ...}"

# AFTER:
f"  Client Secret: ***{'*' * (len(client_secret) - 3)}"
```

### 1.3 Fix Security: Default-Deny Telegram Auth

**C7 — `gateway/platforms/telegram.py:256-262`**
```python
# BEFORE:
if not allowed_csv:
    return True  # allows everyone

# AFTER:
if not allowed_csv:
    logger.warning("TELEGRAM_ALLOWED_USERS not set — denying all callback auth")
    return False
```
**IMPORTANT:** Before deploying, ensure `TELEGRAM_ALLOWED_USERS` is set in `.env` or config.

### 1.4 Fix Security: Shell Injection

**C2 — `tools/environments/docker.py:562-575`**
```python
# BEFORE:
f"(timeout 60 {self._docker_exe} stop {self._container_id} || ..."
subprocess.run(cmd, shell=True, ...)

# AFTER:
subprocess.run(
    ["timeout", "60", self._docker_exe, "stop", self._container_id],
    capture_output=True, text=True, timeout=65,
)
```
Refactor the compound shell command into individual subprocess calls.

**C3 — `tui_gateway/handlers/_misc.py:422-423`**
```python
# BEFORE:
subprocess.run(cmd, shell=True, ...)

# AFTER:
import shlex
subprocess.run(shlex.split(cmd), shell=False, ...)
```

**C4 — `tui_gateway/handlers/_tools.py:257-259` and `cli.py:6179-6180`**
```python
# BEFORE:
subprocess.run(qc.get("command", ""), shell=True, ...)

# AFTER:
import shlex
subprocess.run(shlex.split(qc.get("command", "")), shell=False, ...)
```

### 1.5 Fix .env Permissions

```bash
chmod 600 /Users/fridayos/.hermes/hermes-agent/.env
```

### 1.6 Checkpoint

```bash
# Run existing tests
cd /Users/fridayos/.hermes/hermes-agent
python -m pytest tests/ -x --timeout=60 -q 2>&1 | tail -20

git add -A
git commit -m "Phase 1: critical quick wins — fd leaks, shell injection, API key masking, default-deny Telegram auth"
```

---

## Phase 2: EDGY Mode Stabilization (Persona System)

**Goal:** Replace the fragile 14-point coupling with a clean Persona abstraction. This is the most impactful change for EDGY reliability. **Be extra careful.**

### 2.1 Create Persona Abstraction

**New file: `gateway/personas/__init__.py`**
```python
"""Persona system — encapsulates all behavioral config for a given AI personality."""

from gateway.personas.base import Persona
from gateway.personas.manager import PersonaManager

__all__ = ["Persona", "PersonaManager"]
```

**New file: `gateway/personas/base.py`**
```python
"""Abstract base class for AI personas."""
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional


class Persona(ABC):
    """A persona encapsulates ALL behavioral differences between personalities.

    Instead of scattered if/else checks across 6 files, each persona
    is a single object that answers: what system prompt? what model?
    what TTS voice? what voice mode?
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique persona identifier (e.g. 'friday', 'sydney')."""

    @abstractmethod
    def system_prompt_path(self, hermes_home: Path) -> Path:
        """Path to the system prompt markdown file."""

    def model_override(self) -> dict:
        """Model override dict. Return {} to use the default model."""
        return {}

    def voice_mode(self) -> str:
        """Voice delivery mode: 'off', 'voice_only', 'text_only', 'both'."""
        return "off"

    def tts_provider(self) -> str:
        """TTS provider name for this persona."""
        return "default"

    def should_suppress_text(self) -> bool:
        """Whether to suppress text reply after sending voice."""
        return self.voice_mode() == "voice_only"

    def health_check(self) -> tuple[bool, str]:
        """Validate external dependencies. Returns (ok, message)."""
        return True, "ok"

    def cache_signature(self) -> str:
        """Unique string for agent cache invalidation."""
        return self.name

    def voice_chunk_keywords(self) -> list[str] | None:
        """Keywords for voice chunking. None = use default sentence splitting."""
        return None
```

**New file: `gateway/personas/friday.py`**
```python
"""Default Friday persona."""
from pathlib import Path
from gateway.personas.base import Persona


class FridayPersona(Persona):

    @property
    def name(self) -> str:
        return "friday"

    def system_prompt_path(self, hermes_home: Path) -> Path:
        return hermes_home / "SOUL.md"
```

**New file: `gateway/personas/sydney.py`**
```python
"""Sydney (EDGY) persona — Qwen3.6 Heretic + Chatterbox Turbo voice clone."""
import os
import logging
from pathlib import Path
from typing import Dict, Optional

from gateway.personas.base import Persona

logger = logging.getLogger(__name__)


class SydneyPersona(Persona):

    @property
    def name(self) -> str:
        return "sydney"

    def system_prompt_path(self, hermes_home: Path) -> Path:
        return hermes_home / "SYDNEY.md"

    def model_override(self) -> dict:
        return {
            "model": os.getenv("EDGY_MODEL", "Qwen3.6-35B-A3B-Heretic-MLX-mxfp8"),
            "provider": "custom",
            "base_url": os.getenv("EDGY_LLM_URL", "http://127.0.0.1:8000/v1"),
            "api_key": "local-no-key",
            "api_mode": "chat",
        }

    def voice_mode(self) -> str:
        return "voice_only"

    def tts_provider(self) -> str:
        return "chatterbox"

    def health_check(self) -> tuple[bool, str]:
        """Verify local LLM server is reachable."""
        try:
            import requests
            llm_url = os.getenv("EDGY_LLM_URL", "http://127.0.0.1:8000/v1")
            resp = requests.get(f"{llm_url}/models", timeout=3)
            if resp.status_code == 200:
                return True, "ok"
            return False, f"LLM server returned {resp.status_code}"
        except Exception as e:
            return False, f"LLM server unreachable: {e}"

    def voice_chunk_keywords(self) -> list[str]:
        return [
            "daddy", "sir", "johnny", "want more", "keep going",
            "want to know", "should i", "do you want", "want me",
            "tell me", "know what happened",
        ]
```

**New file: `gateway/personas/manager.py`**
```python
"""PersonaManager — session-scoped persona state (replaces edgy_state.py)."""
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from gateway.personas.base import Persona
from gateway.personas.friday import FridayPersona
from gateway.personas.sydney import SydneyPersona

logger = logging.getLogger(__name__)


class PersonaManager:
    """Manages active persona per session. Thread-safe. In-memory only.

    Replaces:
      - gateway/edgy_state.py (_edgy_sessions, _edgy_chat_ids, threading.local)
      - gateway/run.py _voice_mode + _session_model_overrides for persona config
      - Monkey-patched agent._edgy_mode for cache invalidation
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, Persona] = {}  # session_key → active persona
        self._default = FridayPersona()
        self._registry: Dict[str, Persona] = {
            "friday": self._default,
            "sydney": SydneyPersona(),
        }

    def register(self, name: str, persona: Persona) -> None:
        """Register a new persona type."""
        with self._lock:
            self._registry[name] = persona

    def activate(self, session_key: str, persona_name: str) -> Persona:
        """Switch session to a named persona. Returns the persona for health check."""
        persona = self._registry.get(persona_name)
        if not persona:
            raise ValueError(f"Unknown persona: {persona_name}")
        with self._lock:
            self._sessions[session_key] = persona
        logger.info("Persona activated: session=%s persona=%s", session_key, persona_name)
        return persona

    def deactivate(self, session_key: str) -> Persona:
        """Revert session to default persona."""
        with self._lock:
            self._sessions.pop(session_key, None)
        logger.info("Persona deactivated: session=%s → friday", session_key)
        return self._default

    def current(self, session_key: str) -> Persona:
        """Get active persona for a session. Returns default if none set."""
        with self._lock:
            return self._sessions.get(session_key, self._default)

    def is_active(self, session_key: str, persona_name: str) -> bool:
        """Check if a specific persona is active for a session."""
        with self._lock:
            p = self._sessions.get(session_key)
            return p is not None and p.name == persona_name

    def active_persona_name(self, session_key: str) -> str:
        """Get the name of the active persona."""
        return self.current(session_key).name

    def list_personas(self) -> list[str]:
        """List all registered persona names."""
        return list(self._registry.keys())
```

### 2.2 Wire Persona into GatewayRunner

**Edit `gateway/run.py` — `/edgy` handler (L5929-5964):**

```python
# BEFORE (5 scattered mutations):
async def _handle_edgy_command(self, event: MessageEvent) -> str:
    from gateway.edgy_state import set_edgy_session, set_current_thread_edgy
    # ... 5 separate state changes ...

# AFTER (single activation):
async def _handle_edgy_command(self, event: MessageEvent) -> str:
    source = event.source
    session_key = self._session_key_for_source(source)

    persona = self._personas.activate(session_key, "sydney")

    # Health check before confirming
    ok, msg = persona.health_check()
    if not ok:
        self._personas.deactivate(session_key)
        return f"Can't reach the local LLM ({msg}). Make sure oMLX is running."

    # Still need voice_mode and cache eviction (these are GatewayRunner-level concerns)
    voice_key = self._voice_key(source.platform, source.chat_id)
    self._voice_mode[voice_key] = persona.voice_mode()
    self._save_voice_modes()
    self._evict_cached_agent(session_key)

    return (
        "🔥 *Edgy mode activated*\n\n"
        "I'm Sydney now — all voice, no text. "
        "Ask me anything~"
    )
```

**Edit `gateway/run.py` — `/normal` handler (L5966-5990):**

```python
# AFTER:
async def _handle_normal_command(self, event: MessageEvent) -> str:
    source = event.source
    session_key = self._session_key_for_source(source)

    self._personas.deactivate(session_key)

    voice_key = self._voice_key(source.platform, source.chat_id)
    self._voice_mode[voice_key] = "off"
    self._save_voice_modes()
    self._session_model_overrides.pop(session_key, None)
    self._evict_cached_agent(session_key)

    return "Back to normal mode. Friday is here."
```

### 2.3 Wire Persona into Agent Creation

**Edit `gateway/run.py` — pre-agent run (L9900-9914):**

```python
# BEFORE (thread-local propagation):
_is_edgy_for_thread = False
if _resolved_session_key:
    try:
        from gateway.edgy_state import is_edgy_session, set_current_thread_edgy
        _is_edgy_for_thread = is_edgy_session(session_key=_resolved_session_key)
    except Exception:
        pass
set_current_thread_edgy(_is_edgy_for_thread)

# AFTER (session-scoped persona lookup):
_persona = self._personas.current(_resolved_session_key) if _resolved_session_key else self._personas._default
```

**Edit `gateway/run.py` — cache invalidation (L10070-10141):**

```python
# BEFORE (monkey-patched attribute):
_cached_edgy = getattr(cached[0], "_edgy_mode", False)
if _cached_edgy != _is_edgy:
    del _cache[session_key]

# AFTER (persona cache signature):
_cache_persona_sig = getattr(cached[0], "_persona_sig", "friday")
if _cache_persona_sig != _persona.cache_signature():
    del _cache[session_key]

# ... on agent creation:
agent._persona_sig = _persona.cache_signature()
```

**Edit `gateway/run.py` — model override:**

```python
# BEFORE:
_model_override = self._session_model_overrides.get(session_key, {})

# AFTER:
_model_override = _persona.model_override()
# If persona has an override, use it; otherwise check legacy _session_model_overrides
if not _model_override:
    _model_override = self._session_model_overrides.get(session_key, {})
```

### 2.4 Deduplicate Persona Loading

**Create `agent/persona_loader.py`** — single source of truth for system prompt loading:

```python
"""Single source of truth for loading the active persona's system prompt."""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_active_system_prompt(
    hermes_home: Path,
    persona_name: str = "friday",
    skip_context_files: bool = False,
) -> Optional[str]:
    """Load the system prompt for the active persona.

    This replaces the duplicated logic that was in both
    agent/message_builder.py and run_agent.py.
    """
    if skip_context_files:
        return None

    if persona_name == "sydney":
        sydney_path = hermes_home / "SYDNEY.md"
        if sydney_path.exists():
            try:
                content = sydney_path.read_text(encoding="utf-8").strip()
                if content:
                    logger.debug("Loaded Sydney persona from %s", sydney_path)
                    return content
            except Exception as e:
                logger.warning("Failed to load SYDNEY.md: %s", e)

    # Default: load SOUL.md
    soul_path = hermes_home / "SOUL.md"
    if soul_path.exists():
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Loaded Friday persona from %s", soul_path)
                return content
        except Exception as e:
            logger.warning("Failed to load SOUL.md: %s", e)

    return None
```

**Edit `agent/message_builder.py:56-71`:**

```python
# BEFORE (duplicated edgy check):
_is_edgy = False
try:
    from gateway.edgy_state import is_current_thread_edgy
    _is_edgy = is_current_thread_edgy()
except Exception:
    pass
if _is_edgy:
    _sydney_path = get_hermes_home() / "SYDNEY.md"
    # ... load SYDNEY.md ...

# AFTER (unified loader):
from agent.persona_loader import load_active_system_prompt
_persona_name = getattr(self, '_active_persona_name', 'friday')
_prompt_content = load_active_system_prompt(
    get_hermes_home(),
    persona_name=_persona_name,
    skip_context_files=self.skip_context_files,
)
if _prompt_content:
    prompt_parts = [_prompt_content]
    _soul_loaded = True
```

**Edit `run_agent.py:4078-4098`:**

```python
# Same replacement — use load_active_system_prompt() instead of duplicated logic
```

### 2.5 Pass Persona Name Through Agent Context

Instead of thread-local, pass persona name explicitly:

**Edit `gateway/run.py` — agent creation site:**

```python
# When creating AIAgent, pass persona name:
agent = AIAgent(
    model=turn_route["model"],
    # ... existing params ...
)

# Store persona name ON the agent for message_builder to read:
agent._active_persona_name = _persona.name
```

**Edit `agent/message_builder.py` — read from agent context:**

```python
# In build_messages() or wherever persona is checked:
# Read persona name from the agent object (set by gateway)
# instead of thread-local
_persona_name = getattr(self._agent_ref, '_active_persona_name', 'friday')
```

**NOTE:** This requires passing agent reference to message builder, or passing persona name as an explicit parameter. The exact wiring depends on how message_builder is invoked. Need to trace the call chain and pick the cleanest injection point.

### 2.6 Deduplicate TTS Code

**Consolidate into `tools/tts/providers.py` only:**

```python
# tools/tts/providers.py — keep the canonical version:
def _generate_chatterbox_tts(text: str, output_path: str, tts_config: dict) -> str:
    import requests
    ch_config = tts_config.get("chatterbox", {})
    base_url = ch_config.get("url", tts_config.get("sydney", {}).get("url", "http://localhost:9001/v1/audio/speech"))
    model = ch_config.get("model", "chatterbox-turbo")  # standardize default
    want_opus = output_path.endswith(".ogg")
    payload = {
        "model": model,
        "input": text,
        "response_format": "opus" if want_opus else "wav",
    }
    response = requests.post(base_url, json=payload, timeout=30)  # reduced from 120
    if response.status_code != 200:
        raise RuntimeError(f"Sydney TTS server returned {response.status_code}: {response.text[:200]}")
    audio_data = response.content
    if not audio_data:
        raise RuntimeError("Sydney TTS server returned empty audio")
    with open(output_path, "wb") as f:
        f.write(audio_data)
    return output_path
```

**Delete the duplicate from `tools/tts_tool.py:553-590`** and import from providers:

```python
# tools/tts_tool.py
from tools.tts.providers import _generate_chatterbox_tts
```

### 2.7 Make Voice Chunking Persona-Aware

**Edit `gateway/run.py:6384-6410`:**

```python
def _chunkify_voice_text(self, text: str, persona=None) -> list[str]:
    """Split text into voice chunks. Uses persona-specific hook keywords if available."""
    # ... existing splitting logic ...

    # Hook keywords: persona-specific or default
    if persona and persona.voice_chunk_keywords():
        hook_words = "|".join(persona.voice_chunk_keywords())
    else:
        hook_words = None  # no hook-based splitting

    hook_keywords_pat = re.compile(
        rf"(?:{hook_words})",
        re.IGNORECASE,
    ) if hook_words else None

    # ... rest of chunking logic, only use hook_keywords_pat if not None ...
```

**Edit voice reply sending to pass persona:**

```python
# Where _chunkify_voice_text is called:
_persona = self._personas.current(session_key)
chunks = self._chunkify_voice_text(text, persona=_persona)
```

### 2.8 Cleanup: Remove Old edgy_state.py

After all references are migrated:

```bash
# Verify no remaining imports:
grep -rn "edgy_state" --include="*.py" | grep -v __pycache__ | grep -v ".plans/"
# Should return 0 results

# Then delete:
rm gateway/edgy_state.py
```

### 2.9 Add Tests

**New file: `tests/test_persona_system.py`**

```python
"""Tests for the persona system."""
import pytest
from gateway.personas import PersonaManager
from gateway.personas.friday import FridayPersona
from gateway.personas.sydney import SydneyPersona


class TestPersonaManager:

    def setup_method(self):
        self.pm = PersonaManager()

    def test_default_is_friday(self):
        p = self.pm.current("any-session")
        assert p.name == "friday"

    def test_activate_sydney(self):
        self.pm.activate("s1", "sydney")
        p = self.pm.current("s1")
        assert p.name == "sydney"
        assert p.voice_mode() == "voice_only"

    def test_deactivate_returns_to_friday(self):
        self.pm.activate("s1", "sydney")
        self.pm.deactivate("s1")
        assert self.pm.current("s1").name == "friday"

    def test_sessions_independent(self):
        self.pm.activate("s1", "sydney")
        assert self.pm.current("s2").name == "friday"
        assert self.pm.current("s1").name == "sydney"

    def test_sydney_model_override(self):
        p = self.pm._registry["sydney"]
        override = p.model_override()
        assert "Qwen" in override["model"]
        assert "127.0.0.1:8000" in override["base_url"]

    def test_unknown_persona_raises(self):
        with pytest.raises(ValueError, match="Unknown persona"):
            self.pm.activate("s1", "nonexistent")

    def test_cache_signature_differs(self):
        assert self.pm._registry["friday"].cache_signature() != self.pm._registry["sydney"].cache_signature()

    def test_friday_no_model_override(self):
        assert self.pm._default.model_override() == {}

    def test_friday_no_suppress_text(self):
        assert not self.pm._default.should_suppress_text()

    def test_sydney_suppresses_text(self):
        assert self.pm._registry["sydney"].should_suppress_text()


class TestSydneyPersona:

    def test_voice_chunk_keywords(self):
        p = SydneyPersona()
        keywords = p.voice_chunk_keywords()
        assert "daddy" in keywords
        assert "johnny" in keywords
        assert len(keywords) > 5

    def test_system_prompt_path(self, tmp_path):
        p = SydneyPersona()
        path = p.system_prompt_path(tmp_path)
        assert path.name == "SYDNEY.md"

    def test_health_check_unreachable(self):
        """Health check should fail gracefully when LLM server is down."""
        p = SydneyPersona()
        # This test assumes no server on port 8000 in test env
        ok, msg = p.health_check()
        # We don't assert True/False since test env varies
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
```

### 2.10 Checkpoint

```bash
python -m pytest tests/test_persona_system.py -v

git add gateway/personas/ agent/persona_loader.py tests/test_persona_system.py
git commit -m "Phase 2: persona system — replaces edgy_state.py with PersonaManager"

# Wire into gateway/run.py and verify
python -m pytest tests/ -x --timeout=60 -q 2>&1 | tail -20

git add gateway/run.py agent/message_builder.py run_agent.py tools/tts/
git commit -m "Phase 2: wire persona system into gateway, deduplicate persona/TTS code"

# Remove old edgy_state after verification
rm gateway/edgy_state.py
git add -A
git commit -m "Phase 2: remove legacy edgy_state.py"
```

---

## Phase 3: Image Generation Pipeline Stabilization

**Goal:** Make image generation reliable, persona-aware, and less fragile.

### 3.1 Audit Current Image Generation Flow

Before changing code, trace the exact call chain:

1. LLM (Qwen3.6) calls `image_generate` tool with prompt
2. `tools/image_generation_tool.py:_handle_image_generate()` is invoked
3. It imports `comfyui_image_gen.py` from `~/.hermes/scripts/`
4. ComfyUI receives prompt with `sw33ny` trigger for Sydney LoRA
5. Generated image path returned with `MEDIA:` prefix
6. Gateway delivers as Telegram photo

**Known fragility points:**
- `comfyui_image_gen.py` is an external script — import failure = crash
- No fallback if ComfyUI (port 8188) is down
- Sydney LoRA trigger (`sw33ny`) is in SYDNEY.md, not in the tool config
- Tool schema description mentions Sydney by name (not persona-aware)
- No timeout on ComfyUI generation (can hang indefinitely)
- No retry on transient ComfyUI failures

### 3.2 Make Image Tool Persona-Aware

**Edit `tools/image_generation_tool.py`:**

```python
# Add persona awareness to image generation
def _handle_image_generate(args, **kw):
    prompt = args.get("prompt", "")
    # ... existing parsing ...

    # Determine if Sydney persona is active (via session context or thread-local)
    _is_sydney = False
    try:
        from gateway.personas import PersonaManager
        # Read persona from some session context
        # This needs careful wiring — see note below
    except Exception:
        pass

    # If Sydney persona, ensure trigger word is present
    if _is_sydney and "sw33ny" not in prompt.lower():
        prompt = f"sw33ny, {prompt}"

    # ... rest of generation logic ...
```

**Better approach:** Let the LLM handle trigger words via persona instructions (as it does now via SYDNEY.md), and make the tool itself persona-agnostic. The tool should:
1. Validate ComfyUI is reachable before attempting
2. Have a configurable timeout
3. Have retry logic for transient failures
4. Clean up temp files

### 3.3 Add ComfyUI Health Check and Timeout

**Edit `tools/image_generation_tool.py`:**

```python
COMFYUI_TIMEOUT = 120  # seconds
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")


def _check_comfyui_health() -> tuple[bool, str]:
    """Check if ComfyUI server is reachable."""
    try:
        import requests
        resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        return resp.status_code == 200, "ok"
    except Exception as e:
        return False, str(e)


def _handle_image_generate(args, **kw):
    prompt = args.get("prompt", "")
    # ... existing code ...

    # Health check before generation
    ok, msg = _check_comfyui_health()
    if not ok:
        return json.dumps({
            "error": f"ComfyUI server unreachable: {msg}. "
                     "Make sure ComfyUI is running on port 8188."
        })

    # ... generation with timeout ...
```

### 3.4 Add Retry Logic

```python
import time

def _generate_with_retry(prompt, aspect_ratio, max_retries=2):
    """Generate image with retry on transient failures."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = _comfy_generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                timeout=COMFYUI_TIMEOUT,
            )
            return result
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 1s, 2s backoff
                continue
            raise
```

### 3.5 Clean Up Generated Files After Delivery

Currently, generated images are saved to disk but never cleaned up. Add cleanup:

```python
# After successful Telegram delivery, schedule cleanup
import asyncio

async def _cleanup_image_after_delivery(image_path: str, delay: int = 300):
    """Delete generated image 5 minutes after delivery to Telegram."""
    await asyncio.sleep(delay)
    try:
        os.remove(image_path)
    except Exception:
        pass
```

### 3.6 Checkpoint

```bash
python -m pytest tests/ -x --timeout=60 -q 2>&1 | tail -20

git add tools/image_generation_tool.py
git commit -m "Phase 3: image gen — health check, timeout, retry, cleanup"
```

---

## Phase 4: TTS Pipeline Hardening

**Goal:** Make the Chatterbox/Sydney TTS pipeline resilient.

### 4.1 Add TTS Server Watchdog

**Edit `gateway/run.py`:**

Add a background health check that restarts `sydney_tts_server.py` if it dies:

```python
async def _tts_server_watchdog(self):
    """Periodically check Sydney TTS server health, restart if needed."""
    TTS_CHECK_INTERVAL = 60  # seconds
    while self._running:
        try:
            import requests
            resp = requests.get("http://127.0.0.1:9001/health", timeout=5)
            if resp.status_code != 200:
                logger.warning("Sydney TTS server unhealthy, attempting restart")
                await self._restart_tts_server()
        except Exception:
            logger.warning("Sydney TTS server unreachable, attempting restart")
            await self._restart_tts_server()
        await asyncio.sleep(TTS_CHECK_INTERVAL)
```

### 4.2 Add Retry to Chatterbox TTS Provider

**Edit `tools/tts/providers.py` — `_generate_chatterbox_tts`:**

```python
def _generate_chatterbox_tts(text: str, output_path: str, tts_config: dict) -> str:
    import requests
    ch_config = tts_config.get("chatterbox", {})
    base_url = ch_config.get("url", tts_config.get("sydney", {}).get("url", "http://localhost:9001/v1/audio/speech"))
    model = ch_config.get("model", "chatterbox-turbo")
    want_opus = output_path.endswith(".ogg")
    payload = {
        "model": model,
        "input": text,
        "response_format": "opus" if want_opus else "wav",
    }

    last_error = None
    for attempt in range(3):  # 3 attempts
        try:
            response = requests.post(base_url, json=payload, timeout=30)
            if response.status_code == 200 and response.content:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return output_path
            last_error = RuntimeError(f"TTS server returned {response.status_code}: {response.text[:200]}")
        except requests.Timeout:
            last_error = RuntimeError("TTS server timed out after 30s")
        except requests.ConnectionError:
            last_error = RuntimeError("TTS server connection refused")

        if attempt < 2:
            import time
            time.sleep(2 ** attempt)  # 1s, 2s backoff

    raise last_error or RuntimeError("TTS generation failed")
```

### 4.3 Fix Temp File Cleanup in TTS Server

**Edit `~/.hermes/sydney/sydney_tts_server.py`:**

Ensure `TemporaryDirectory` cleanup uses context managers:

```python
# Use context manager for temp dirs instead of manual tracking
import tempfile

@app.route('/v1/audio/speech', methods=['POST'])
def generate_speech():
    with tempfile.TemporaryDirectory(prefix="sydney_tts_") as tmpdir:
        # ... generate into tmpdir ...
        # ... send file ...
        # Context manager auto-cleans on exit
```

### 4.4 Checkpoint

```bash
git add tools/tts/providers.py gateway/run.py
git commit -m "Phase 4: TTS hardening — retry logic, reduced timeout, temp file cleanup"
```

---

## Phase 5: System-Wide Hardening

**Goal:** Address remaining HIGH/MEDIUM findings from the audit.

### 5.1 Per-User Rate Limiting

**New file: `gateway/rate_limiter.py`**

```python
"""Token bucket rate limiter for per-user request throttling."""
import time
import threading
from typing import Dict


class RateLimiter:
    def __init__(self, max_tokens: int = 10, refill_rate: float = 1.0):
        self._max = max_tokens
        self._rate = refill_rate
        self._buckets: Dict[str, list] = {}  # key → [tokens, last_refill]
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            if key not in self._buckets:
                self._buckets[key] = [self._max, now]
            tokens, last = self._buckets[key]
            tokens = min(self._max, tokens + (now - last) * self._rate)
            if tokens >= 1:
                self._buckets[key] = [tokens - 1, now]
                return True
            self._buckets[key] = [tokens, now]
            return False
```

**Wire into `gateway/platforms/telegram.py`:**

```python
# At message handler entry:
if not self._rate_limiter.allow(str(event.source.chat_id)):
    await event.reply("Slow down! Please wait a moment.")
    return
```

### 5.2 Per-User Memory Isolation

**Edit `tools/memory_tool.py`:**

```python
# BEFORE:
def get_memory_dir() -> Path:
    return get_hermes_home() / "memories"

# AFTER:
def get_memory_dir(user_id: str = "") -> Path:
    base = get_hermes_home() / "memories"
    if user_id and user_id != "default":
        user_dir = base / "users" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
    return base
```

Requires passing user_id from gateway session context to the tool.

### 5.3 Shared HTTP Session Pool

**New file: `tools/http_pool.py`**

```python
"""Shared HTTP session pool for all tools."""
import aiohttp
import requests
from typing import Optional

_sync_session: Optional[requests.Session] = None

def get_sync_session() -> requests.Session:
    """Get or create a shared requests.Session with connection pooling."""
    global _sync_session
    if _sync_session is None:
        _sync_session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=3,
        )
        _sync_session.mount("http://", adapter)
        _sync_session.mount("https://", adapter)
    return _sync_session
```

**Gradually replace** bare `requests.post()`/`requests.get()` calls across tools with `get_sync_session().post()`.

### 5.4 Checkpoint

```bash
python -m pytest tests/ -x --timeout=60 -q 2>&1 | tail -20

git add gateway/rate_limiter.py tools/memory_tool.py tools/http_pool.py
git commit -m "Phase 5: rate limiting, memory isolation, HTTP session pool"
```

---

## Phase 6: Cleanup and Documentation

### 6.1 Remove Dead Code

- Delete `gateway/edgy_state.py` (if not already done)
- Remove any remaining `from gateway.edgy_state import ...` references
- Clean up the 13 manual `json.dumps({"error": ...})` calls in tools (use `tool_error()`)

### 6.2 Update AGENTS.md

Add persona system documentation:
- How to register a new persona
- How the persona lifecycle works
- Configuration via YAML

### 6.3 Final Test Run

```bash
# Full test suite
python -m pytest tests/ -v --timeout=120

# Manual smoke test EDGY mode:
# 1. Start gateway
# 2. Send /edgy in Telegram
# 3. Verify Sydney persona loads
# 4. Send a message — verify voice-only response
# 5. Request image generation — verify ComfyUI delivers
# 6. Send /normal — verify Friday returns
# 7. Kill oMLX server, send /edgy — verify health check catches it
# 8. Restart oMLX, send /edgy — verify activation works
```

### 6.4 Merge

```bash
# Review all changes
git log --oneline audit/hardening-and-personas

# Merge to johnny
git checkout johnny
git merge audit/hardening-and-personas
```

---

## Rollback Strategy

Each phase is a separate commit on a feature branch. If any phase causes issues:

```bash
# Roll back to a specific phase:
git checkout johnny
git revert <commit-hash>

# Or abandon the entire branch:
git checkout johnny
git branch -D audit/hardening-and-personas
```

The persona system (Phase 2) is designed to be backward-compatible. If the PersonaManager has a bug, the gateway can fall back to the old behavior by re-adding `edgy_state.py` and reverting the `/edgy` and `/normal` handlers.

---

## Risk Assessment Per Phase

| Phase | Risk Level | What Could Break | Mitigation |
|---|---|---|---|
| 1 (Quick Wins) | **Low** | Minor behavior changes (key masking, auth deny) | Test that Telegram still works with ALLOWED_USERS set |
| 2 (Personas) | **Medium-High** | EDGY toggle, persona loading, cache invalidation | Comprehensive tests; keep edgy_state.py as fallback until verified |
| 3 (Image Gen) | **Medium** | ComfyUI integration, image delivery | Health check catches server-down; retry handles transient failures |
| 4 (TTS) | **Low-Medium** | Voice delivery, TTS timeout | Retry logic handles transient failures; reduced timeout prevents hangs |
| 5 (Hardening) | **Low** | Rate limiting, memory paths | Feature-gated; defaults match current behavior |
| 6 (Cleanup) | **Very Low** | Documentation only | N/A |

**Highest risk is Phase 2.** It touches the most code paths and changes how EDGY mode state flows. The mitigation is:
1. Write tests FIRST, then change code
2. Keep the old code commented out (not deleted) until manual smoke test passes
3. Test with actual Telegram bot before merging

---

## Estimated Effort

| Phase | Time | Complexity |
|---|---|---|
| 1: Quick Wins | 2-3 hours | Simple find-and-replace |
| 2: Persona System | 6-8 hours | Architecture change + careful wiring |
| 3: Image Generation | 3-4 hours | Needs ComfyUI testing environment |
| 4: TTS Hardening | 2-3 hours | Small targeted changes |
| 5: System Hardening | 4-5 hours | New components (rate limiter, pool) |
| 6: Cleanup | 1-2 hours | Documentation + dead code removal |
| **Total** | **18-25 hours** | Spread over 4-5 sessions |
