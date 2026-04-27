"""Tests for the persona system — PersonaManager, personas, and persona_loader."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from gateway.personas import PersonaManager
from gateway.personas.base import Persona
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

    def test_is_active(self):
        assert not self.pm.is_active("s1", "sydney")
        self.pm.activate("s1", "sydney")
        assert self.pm.is_active("s1", "sydney")
        assert not self.pm.is_active("s1", "friday")
        assert not self.pm.is_active("s2", "sydney")

    def test_active_persona_name(self):
        assert self.pm.active_persona_name("s1") == "friday"
        self.pm.activate("s1", "sydney")
        assert self.pm.active_persona_name("s1") == "sydney"

    def test_list_personas(self):
        names = self.pm.list_personas()
        assert "friday" in names
        assert "sydney" in names

    def test_register_custom_persona(self):
        class CustomPersona(Persona):
            @property
            def name(self):
                return "custom"
            def system_prompt_path(self, hermes_home):
                return hermes_home / "CUSTOM.md"

        self.pm.register("custom", CustomPersona())
        assert "custom" in self.pm.list_personas()
        self.pm.activate("s1", "custom")
        assert self.pm.current("s1").name == "custom"


class TestSydneyPersona:

    def test_voice_chunk_keywords(self):
        p = SydneyPersona()
        keywords = p.voice_chunk_keywords()
        assert "daddy" in keywords
        assert "johnny" in keywords
        assert len(keywords) > 5

    def test_voice_chunk_max(self):
        p = SydneyPersona()
        assert p.voice_chunk_max() == 300

    def test_tts_timeout(self):
        p = SydneyPersona()
        assert p.tts_timeout() == 90

    def test_system_prompt_path(self, tmp_path):
        p = SydneyPersona()
        path = p.system_prompt_path(tmp_path)
        assert path.name == "SYDNEY.md"

    def test_tts_provider(self):
        p = SydneyPersona()
        assert p.tts_provider() == "chatterbox"

    def test_voice_mode(self):
        p = SydneyPersona()
        assert p.voice_mode() == "voice_only"

    def test_health_check_returns_tuple(self):
        p = SydneyPersona()
        ok, msg = p.health_check()
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_model_override_env_vars(self):
        p = SydneyPersona()
        with patch.dict("os.environ", {"EDGY_MODEL": "test-model", "EDGY_LLM_URL": "http://test:9999/v1"}):
            override = p.model_override()
            assert override["model"] == "test-model"
            assert override["base_url"] == "http://test:9999/v1"


class TestFridayPersona:

    def test_system_prompt_path(self, tmp_path):
        p = FridayPersona()
        path = p.system_prompt_path(tmp_path)
        assert path.name == "SOUL.md"

    def test_no_voice_chunk_keywords(self):
        p = FridayPersona()
        assert p.voice_chunk_keywords() is None

    def test_voice_mode_off(self):
        p = FridayPersona()
        assert p.voice_mode() == "off"

    def test_default_chunk_max(self):
        p = FridayPersona()
        assert p.voice_chunk_max() == 900

    def test_default_tts_timeout(self):
        p = FridayPersona()
        assert p.tts_timeout() == 60

    def test_no_health_check_needed(self):
        p = FridayPersona()
        ok, msg = p.health_check()
        assert ok is True


class TestPersonaLoader:

    def test_load_friday_prompt(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        soul = tmp_path / "SOUL.md"
        soul.write_text("I am Friday")
        result = load_active_system_prompt(tmp_path, "friday")
        assert result == "I am Friday"

    def test_load_sydney_prompt(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        sydney = tmp_path / "SYDNEY.md"
        sydney.write_text("I am Sydney")
        result = load_active_system_prompt(tmp_path, "sydney")
        assert result == "I am Sydney"

    def test_sydney_falls_back_to_soul(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        # No SYDNEY.md, but SOUL.md exists
        soul = tmp_path / "SOUL.md"
        soul.write_text("I am Friday")
        result = load_active_system_prompt(tmp_path, "sydney")
        assert result == "I am Friday"

    def test_skip_context_files(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        result = load_active_system_prompt(tmp_path, "friday", skip_context_files=True)
        assert result is None

    def test_no_prompt_file(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        result = load_active_system_prompt(tmp_path, "friday")
        assert result is None

    def test_empty_prompt_file(self, tmp_path):
        from agent.persona_loader import load_active_system_prompt
        soul = tmp_path / "SOUL.md"
        soul.write_text("   \n\n  ")
        result = load_active_system_prompt(tmp_path, "friday")
        assert result is None


class TestVoiceChunking:
    """Test that voice chunking respects persona keywords."""

    def test_friday_no_hook_splitting(self):
        """Friday persona should not split on hook keywords."""
        from gateway.personas.friday import FridayPersona
        from gateway.personas.sydney import SydneyPersona

        # Simulate the keyword pattern behavior
        friday = FridayPersona()
        assert friday.voice_chunk_keywords() is None

    def test_sydney_has_hook_keywords(self):
        """Sydney persona provides hook keywords for chunking."""
        sydney = SydneyPersona()
        keywords = sydney.voice_chunk_keywords()
        assert keywords is not None
        assert len(keywords) > 0
