"""
TTS Provider Registry and Generator Functions

Each provider is a callable: (text: str, output_path: str, tts_config: Dict) -> str
Registered in PROVIDER_DISPATCH below.

Adding a new provider:
  1. Add _generate_<provider> function below
  2. Add entry to PROVIDER_DISPATCH
  3. Optionally add PROVIDER_MAX_TEXT_LENGTH entry
"""

import base64
import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Constants (duplicated here to avoid circular imports from tts_tool.py)
# -----------------------------------------------------------------------
DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MINIMAX_MODEL = "speech-2.8-hd"
DEFAULT_MINIMAX_VOICE_ID = "English_Graceful_Lady"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1/t2a_v2"
DEFAULT_MISTRAL_TTS_MODEL = "voxtral-mini-tts-2603"
DEFAULT_MISTRAL_TTS_VOICE_ID = "c69964a6-ab8b-4f8a-9465-ec0925096ec8"
DEFAULT_XAI_VOICE_ID = "eve"
DEFAULT_XAI_LANGUAGE = "en"
DEFAULT_XAI_SAMPLE_RATE = 24000
DEFAULT_XAI_BIT_RATE = 128000
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_TTS_VOICE = "Kore"
DEFAULT_GEMINI_TTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TTS_SAMPLE_RATE = 24000
GEMINI_TTS_CHANNELS = 1
GEMINI_TTS_SAMPLE_WIDTH = 2
DEFAULT_KITTENTTS_MODEL = "KittenML/kitten-tts-nano-0.8-int8"
DEFAULT_KITTENTTS_VOICE = "Jasper"
DEFAULT_CHATTERBOX_MODEL = "chatterbox-turbo"

PROVIDER_MAX_TEXT_LENGTH: Dict[str, int] = {
    "edge": 5000,
    "openai": 4096,
    "xai": 15000,
    "minimax": 10000,
    "mistral": 4000,
    "gemini": 5000,
    "elevenlabs": 10000,
    "chatterbox": 8000,
    "neutts": 2000,
    "kittentts": 2000,
}

ELEVENLABS_MODEL_MAX_TEXT_LENGTH: Dict[str, int] = {
    "eleven_v3": 5000,
    "eleven_ttv_v3": 5000,
    "eleven_multilingual_v2": 10000,
    "eleven_multilingual_v1": 10000,
    "eleven_english_sts_v2": 10000,
    "eleven_english_sts_v1": 10000,
    "eleven_flash_v2": 30000,
    "eleven_flash_v2_5": 40000,
}

FALLBACK_MAX_TEXT_LENGTH = 4000

# -----------------------------------------------------------------------
# Lazy imports
# -----------------------------------------------------------------------
def _import_elevenlabs():
    from elevenlabs.client import ElevenLabs
    return ElevenLabs

def _import_openai_client():
    from openai import OpenAI as OpenAIClient
    return OpenAIClient

def _import_mistral_client():
    from mistralai.client import Mistral
    return Mistral

def _import_kittentts():
    from kittentts import KittenTTS
    return KittenTTS

def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

# -----------------------------------------------------------------------
# Provider: Edge TTS (free)
# -----------------------------------------------------------------------
async def _generate_edge_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import edge_tts
    edge_config = tts_config.get("edge", {})
    voice = edge_config.get("voice", "en-US-AriaNeural")
    speed = float(edge_config.get("speed", tts_config.get("speed", 1.0)))
    kwargs = {"voice": voice}
    if speed != 1.0:
        pct = round((speed - 1.0) * 100)
        kwargs["rate"] = f"{pct:+d}%"
    communicate = edge_tts.Communicate(text, **kwargs)
    await communicate.save(output_path)
    return output_path

# -----------------------------------------------------------------------
# Provider: ElevenLabs (premium)
# -----------------------------------------------------------------------
def _generate_elevenlabs(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set. Get one at https://elevenlabs.io/")
    el_config = tts_config.get("elevenlabs", {})
    voice_id = el_config.get("voice_id", DEFAULT_ELEVENLABS_VOICE_ID)
    model_id = el_config.get("model_id", DEFAULT_ELEVENLABS_MODEL_ID)
    if output_path.endswith(".ogg"):
        output_format = "opus_48000_64"
    else:
        output_format = "mp3_44100_128"
    ElevenLabs = _import_elevenlabs()
    client = ElevenLabs(api_key=api_key)
    audio_generator = client.text_to_speech.convert(
        text=text, voice_id=voice_id, model_id=model_id, output_format=output_format,
    )
    with open(output_path, "wb") as f:
        for chunk in audio_generator:
            f.write(chunk)
    return output_path

# -----------------------------------------------------------------------
# Provider: OpenAI TTS
# -----------------------------------------------------------------------
def _generate_openai_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    from tools.tool_backend_helpers import resolve_openai_audio_client_config
    api_key, base_url = resolve_openai_audio_client_config()
    oai_config = tts_config.get("openai", {})
    model = oai_config.get("model", DEFAULT_OPENAI_MODEL)
    voice = oai_config.get("voice", DEFAULT_OPENAI_VOICE)
    base_url = oai_config.get("base_url", base_url)
    speed = float(oai_config.get("speed", tts_config.get("speed", 1.0)))
    if output_path.endswith(".ogg"):
        response_format = "opus"
    else:
        response_format = "mp3"
    OpenAIClient = _import_openai_client()
    client = OpenAIClient(api_key=api_key, base_url=base_url)
    try:
        create_kwargs = dict(
            model=model, voice=voice, input=text,
            response_format=response_format,
            extra_headers={"x-idempotency-key": str(uuid.uuid4())},
        )
        if speed != 1.0:
            create_kwargs["speed"] = max(0.25, min(4.0, speed))
        response = client.audio.speech.create(**create_kwargs)
        response.stream_to_file(output_path)
        return output_path
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

# -----------------------------------------------------------------------
# Provider: xAI TTS
# -----------------------------------------------------------------------
def _generate_xai_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import requests
    from tools.xai_http import hermes_xai_user_agent
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("XAI_API_KEY not set. Get one at https://console.x.ai/")
    xai_config = tts_config.get("xai", {})
    voice_id = str(xai_config.get("voice_id", DEFAULT_XAI_VOICE_ID)).strip() or DEFAULT_XAI_VOICE_ID
    language = str(xai_config.get("language", DEFAULT_XAI_LANGUAGE)).strip() or DEFAULT_XAI_LANGUAGE
    sample_rate = int(xai_config.get("sample_rate", DEFAULT_XAI_SAMPLE_RATE))
    bit_rate = int(xai_config.get("bit_rate", DEFAULT_XAI_BIT_RATE))
    base_url = str(
        xai_config.get("base_url") or os.getenv("XAI_BASE_URL") or DEFAULT_XAI_BASE_URL
    ).strip().rstrip("/")
    codec = "wav" if output_path.endswith(".wav") else "mp3"
    payload: Dict[str, Any] = {"text": text, "voice_id": voice_id, "language": language}
    if (
        codec != "mp3" or sample_rate != DEFAULT_XAI_SAMPLE_RATE
        or (codec == "mp3" and bit_rate != DEFAULT_XAI_BIT_RATE)
    ):
        output_format: Dict[str, Any] = {"codec": codec}
        if sample_rate:
            output_format["sample_rate"] = sample_rate
        if codec == "mp3" and bit_rate:
            output_format["bit_rate"] = bit_rate
        payload["output_format"] = output_format
    response = requests.post(
        f"{base_url}/tts",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": hermes_xai_user_agent(),
        },
        json=payload, timeout=60,
    )
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)
    return output_path

# -----------------------------------------------------------------------
# Provider: MiniMax TTS
# -----------------------------------------------------------------------
def _generate_minimax_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import requests
    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not set. Get one at https://platform.minimax.io/")
    mm_config = tts_config.get("minimax", {})
    model = mm_config.get("model", DEFAULT_MINIMAX_MODEL)
    voice_id = mm_config.get("voice_id", DEFAULT_MINIMAX_VOICE_ID)
    speed = mm_config.get("speed", tts_config.get("speed", 1))
    vol = mm_config.get("vol", 1)
    pitch = mm_config.get("pitch", 0)
    base_url = mm_config.get("base_url", DEFAULT_MINIMAX_BASE_URL)
    if output_path.endswith(".wav"):
        audio_format = "wav"
    elif output_path.endswith(".flac"):
        audio_format = "flac"
    else:
        audio_format = "mp3"
    payload = {
        "model": model, "text": text, "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": vol, "pitch": pitch},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": audio_format, "channel": 1},
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    response = requests.post(base_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    result = response.json()
    base_resp = result.get("base_resp", {})
    status_code = base_resp.get("status_code", -1)
    if status_code != 0:
        raise RuntimeError(f"MiniMax TTS API error (code {status_code}): {base_resp.get('status_msg', 'unknown')}")
    hex_audio = result.get("data", {}).get("audio", "")
    if not hex_audio:
        raise RuntimeError("MiniMax TTS returned empty audio data")
    audio_bytes = bytes.fromhex(hex_audio)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    return output_path

# -----------------------------------------------------------------------
# Provider: Sydney/Chatterbox (local mlx-audio via sydney_tts_server :9001)
# -----------------------------------------------------------------------
def _generate_chatterbox_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import requests as _rq
    import time as _time
    ch_config = tts_config.get("chatterbox", {})
    base_url = ch_config.get("url", tts_config.get("sydney", {}).get("url", "http://localhost:9001/v1/audio/speech"))
    model = ch_config.get("model", DEFAULT_CHATTERBOX_MODEL)
    want_opus = output_path.endswith(".ogg")
    payload = {
        "model": model, "input": text,
        "response_format": "opus" if want_opus else "wav",
    }
    # Retry up to 3 times with backoff for transient TTS server failures
    last_error = None
    for attempt in range(3):
        try:
            response = _rq.post(base_url, json=payload, timeout=30)
            if response.status_code == 200 and response.content:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return output_path
            last_error = RuntimeError(
                f"Sydney TTS server returned {response.status_code}: {response.text[:200]}"
            )
        except _rq.Timeout:
            last_error = RuntimeError("Sydney TTS server timed out after 30s")
        except _rq.ConnectionError:
            last_error = RuntimeError("Sydney TTS server connection refused (port 9001)")
        except Exception as exc:
            last_error = RuntimeError(f"Sydney TTS error: {exc}")
        if attempt < 2:
            _time.sleep(2 ** attempt)  # 1s, 2s backoff
    raise last_error or RuntimeError("Sydney TTS generation failed after 3 attempts")

# -----------------------------------------------------------------------
# Provider: Mistral (Voxtral TTS)
# -----------------------------------------------------------------------
def _generate_mistral_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY not set. Get one at https://console.mistral.ai/")
    mi_config = tts_config.get("mistral", {})
    model = mi_config.get("model", DEFAULT_MISTRAL_TTS_MODEL)
    voice_id = mi_config.get("voice_id") or DEFAULT_MISTRAL_TTS_VOICE_ID
    if output_path.endswith(".ogg"):
        response_format = "opus"
    elif output_path.endswith(".wav"):
        response_format = "wav"
    elif output_path.endswith(".flac"):
        response_format = "flac"
    else:
        response_format = "mp3"
    Mistral = _import_mistral_client()
    try:
        with Mistral(api_key=api_key) as client:
            response = client.audio.speech.complete(
                model=model, input=text, voice_id=voice_id, response_format=response_format,
            )
            audio_bytes = base64.b64decode(response.audio_data)
    except ValueError:
        raise
    except Exception as e:
        logger.error("Mistral TTS failed: %s", e, exc_info=True)
        raise RuntimeError(f"Mistral TTS failed: {type(e).__name__}") from e
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    return output_path

# -----------------------------------------------------------------------
# Provider: Google Gemini TTS
# -----------------------------------------------------------------------
def _wrap_pcm_as_wav(
    pcm_bytes: bytes,
    sample_rate: int = GEMINI_TTS_SAMPLE_RATE,
    channels: int = GEMINI_TTS_CHANNELS,
    sample_width: int = GEMINI_TTS_SAMPLE_WIDTH,
) -> bytes:
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    data_size = len(pcm_bytes)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH", b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, sample_width * 8,
    )
    data_chunk_header = struct.pack("<4sI", b"data", data_size)
    riff_size = 4 + len(fmt_chunk) + len(data_chunk_header) + data_size
    riff_header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return riff_header + fmt_chunk + data_chunk_header + pcm_bytes

def _generate_gemini_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import requests
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set. Get one at https://aistudio.google.com/app/apikey")
    gemini_config = tts_config.get("gemini", {})
    model = str(gemini_config.get("model", DEFAULT_GEMINI_TTS_MODEL)).strip() or DEFAULT_GEMINI_TTS_MODEL
    voice = str(gemini_config.get("voice", DEFAULT_GEMINI_TTS_VOICE)).strip() or DEFAULT_GEMINI_TTS_VOICE
    base_url = str(
        gemini_config.get("base_url") or os.getenv("GEMINI_BASE_URL") or DEFAULT_GEMINI_TTS_BASE_URL
    ).strip().rstrip("/")
    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    endpoint = f"{base_url}/models/{model}:generateContent"
    response = requests.post(
        endpoint, params={"key": api_key},
        headers={"Content-Type": "application/json"}, json=payload, timeout=60,
    )
    if response.status_code != 200:
        try:
            err = response.json().get("error", {})
            detail = err.get("message") or response.text[:300]
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(f"Gemini TTS API error (HTTP {response.status_code}): {detail}")
    try:
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        audio_part = next((p for p in parts if "inlineData" in p or "inline_data" in p), None)
        if audio_part is None:
            raise RuntimeError("Gemini TTS response contained no audio data")
        inline = audio_part.get("inlineData") or audio_part.get("inline_data") or {}
        audio_b64 = inline.get("data", "")
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Gemini TTS response was malformed: {e}") from e
    if not audio_b64:
        raise RuntimeError("Gemini TTS returned empty audio data")
    pcm_bytes = base64.b64decode(audio_b64)
    wav_bytes = _wrap_pcm_as_wav(pcm_bytes)
    if output_path.lower().endswith(".wav"):
        with open(output_path, "wb") as f:
            f.write(wav_bytes)
        return output_path
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        wav_path = tmp.name
    try:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            if output_path.lower().endswith(".ogg"):
                cmd = [ffmpeg, "-i", wav_path, "-acodec", "libopus", "-ac", "1",
                       "-b:a", "64k", "-vbr", "off", "-y", "-loglevel", "error", output_path]
            else:
                cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore")[:300]
                raise RuntimeError(f"ffmpeg conversion failed: {stderr}")
        else:
            logger.warning("ffmpeg not found; writing raw WAV to %s", output_path)
            shutil.copyfile(wav_path, output_path)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
    return output_path

# -----------------------------------------------------------------------
# Provider: NeuTTS (local, on-device)
# -----------------------------------------------------------------------
def _check_neutts_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("neutts") is not None
    except Exception:
        return False

def _default_neutts_ref_audio() -> str:
    return str(Path(__file__).parent / "neutts_samples" / "jo.wav")

def _default_neutts_ref_text() -> str:
    return str(Path(__file__).parent / "neutts_samples" / "jo.txt")

def _generate_neutts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import sys
    neutts_config = tts_config.get("neutts", {})
    ref_audio = neutts_config.get("ref_audio", "") or _default_neutts_ref_audio()
    ref_text = neutts_config.get("ref_text", "") or _default_neutts_ref_text()
    model = neutts_config.get("model", "neuphonic/neutts-air-q4-gguf")
    device = neutts_config.get("device", "cpu")
    wav_path = output_path
    if not output_path.endswith(".wav"):
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"
    synth_script = str(Path(__file__).parent / "neutts_synth.py")
    cmd = [
        sys.executable, synth_script,
        "--text", text, "--out", wav_path,
        "--ref-audio", ref_audio, "--ref-text", ref_text,
        "--model", model, "--device", device,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        error_lines = [l for l in stderr.splitlines() if not l.startswith("OK:")]
        raise RuntimeError(f"NeuTTS synthesis failed: {chr(10).join(error_lines) or 'unknown error'}")
    if wav_path != output_path:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            conv_cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            subprocess.run(conv_cmd, check=True, timeout=30)
            os.remove(wav_path)
        else:
            os.rename(wav_path, output_path)
    return output_path

# -----------------------------------------------------------------------
# Provider: KittenTTS (local, lightweight ONNX)
# -----------------------------------------------------------------------
_kittentts_model_cache: Dict[str, Any] = {}

def _check_kittentts_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("kittentts") is not None
    except Exception:
        return False

def _generate_kittentts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    global _kittentts_model_cache
    KittenTTS = _import_kittentts()
    kt_config = tts_config.get("kittentts", {})
    model_name = kt_config.get("model", DEFAULT_KITTENTTS_MODEL)
    voice = kt_config.get("voice", DEFAULT_KITTENTTS_VOICE)
    speed = kt_config.get("speed", 1.0)
    clean_text = kt_config.get("clean_text", True)
    if model_name not in _kittentts_model_cache:
        logger.info("[KittenTTS] Loading model: %s", model_name)
        _kittentts_model_cache[model_name] = KittenTTS(model_name)
        logger.info("[KittenTTS] Model loaded successfully")
    model = _kittentts_model_cache[model_name]
    import soundfile as sf
    audio = model.generate(text, voice=voice, speed=speed, clean_text=clean_text)
    wav_path = output_path
    if not output_path.endswith(".wav"):
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"
    sf.write(wav_path, audio, 24000)
    if wav_path != output_path:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            conv_cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            subprocess.run(conv_cmd, check=True, timeout=30)
            os.remove(wav_path)
        else:
            os.rename(wav_path, output_path)
    return output_path

# -----------------------------------------------------------------------
# Provider: Edge TTS (sync wrapper for the async edge_tts)
# -----------------------------------------------------------------------
def _generate_edge_tts_sync(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    import asyncio
    return asyncio.run(_generate_edge_tts(text, output_path, tts_config))

# -----------------------------------------------------------------------
# Provider Registry
# -----------------------------------------------------------------------
# Each entry: (generator_fn, requires_import_check: bool, import_check_fn: Callable or None)
# For sync providers: generator_fn takes (text, output_path, tts_config)
# For async providers (edge_tts): generator_fn takes (text, output_path, tts_config) — wrapper handles asyncio
PROVIDER_DISPATCH: Dict[str, Callable[..., str]] = {
    "edge": _generate_edge_tts_sync,
    "elevenlabs": _generate_elevenlabs,
    "openai": _generate_openai_tts,
    "minimax": _generate_minimax_tts,
    "xai": _generate_xai_tts,
    "chatterbox": _generate_chatterbox_tts,
    "sydney": _generate_chatterbox_tts,
    "mistral": _generate_mistral_tts,
    "gemini": _generate_gemini_tts,
    "neutts": _generate_neutts,
    "kittentts": _generate_kittentts,
}

IMPORT_ERROR_MESSAGES: Dict[str, str] = {
    "elevenlabs": "ElevenLabs provider selected but 'elevenlabs' package not installed. Run: pip install elevenlabs",
    "openai": "OpenAI provider selected but 'openai' package not installed.",
    "mistral": "Mistral provider selected but 'mistralai' package not installed. Run: pip install 'hermes-agent[mistral]'",
    "kittentts": "KittenTTS provider selected but 'kittentts' package not installed. Run 'hermes setup tts' and choose KittenTTS, or install manually: pip install https://github.com/KittenML/KittenTTS/releases/download/0.8.1/kittentts-0.8.1-py3-none-any.whl",
}

def get_import_check(provider: str) -> tuple[Callable[[], bool], str]:
    """Return (check_fn, error_message) for a provider that needs an import check."""
    if provider == "neutts":
        return (_check_neutts_available, "NeuTTS provider selected but neutts is not installed. Run hermes setup and choose NeuTTS, or install espeak-ng and run python -m pip install -U neutts[all].")
    return (_import_noop, "")

def _import_noop() -> bool:
    return True

def resolve_provider(provider: str, tts_config: Dict[str, Any]) -> Callable[..., str]:
    """Return the generator function for the given provider name."""
    key = provider.lower().strip()
    return PROVIDER_DISPATCH[key]

def get_max_text_length(provider: str, tts_config: Optional[Dict[str, Any]] = None) -> int:
    """Return the max text length for a provider, respecting config overrides."""
    if not provider:
        return FALLBACK_MAX_TEXT_LENGTH
    key = provider.lower().strip()
    cfg = tts_config or {}
    prov_cfg = cfg.get(key) if isinstance(cfg.get(key), dict) else {}
    override = prov_cfg.get("max_text_length") if prov_cfg else None
    if isinstance(override, bool):
        override = None
    if isinstance(override, int) and override > 0:
        return override
    if key == "elevenlabs":
        model_id = (prov_cfg or {}).get("model_id") or DEFAULT_ELEVENLABS_MODEL_ID
        mapped = ELEVENLABS_MODEL_MAX_TEXT_LENGTH.get(str(model_id).strip())
        if mapped:
            return mapped
    return PROVIDER_MAX_TEXT_LENGTH.get(key, FALLBACK_MAX_TEXT_LENGTH)
