#!/usr/bin/env python3
"""
Image Generation Tools Module — ComfyUI backend only.

Provides image generation via local ComfyUI (FluxedUp + Sydney Sweeney LoRA).
No FAL.ai dependency — zero cloud costs, fully local.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

DEFAULT_ASPECT_RATIO = "portrait"
VALID_ASPECT_RATIOS = ("landscape", "square", "portrait")


# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------
def check_image_generation_requirements() -> bool:
    """True when ComfyUI is reachable on port 8188."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:8188/system_stats")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Demo / CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Image Generation Tools — ComfyUI local backend")
    print("=" * 52)
    if check_image_generation_requirements():
        print("ComfyUI: reachable on port 8188")
    else:
        print("ComfyUI: NOT reachable on port 8188")
        print("Start ComfyUI before using this tool.")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": (
        "Generate high-quality images from text prompts via local ComfyUI "
        "(FluxedUp + Sydney Sweeney LoRA). Sends image directly to Telegram "
        "as a photo attachment. Supports text-to-image, image-to-image, "
        "and cum/facial workflow via stacked LoRAs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The text prompt describing the desired image. Be detailed and descriptive.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": "The aspect ratio of the generated image. 'landscape' is 16:9 wide, 'portrait' is 16:9 tall, 'square' is 1:1.",
                "default": DEFAULT_ASPECT_RATIO,
            },
        },
        "required": ["prompt"],
    },
}


# ---------------------------------------------------------------------------
# Plugin provider dispatch  (exists for when other plugins register themselves;
# this module does not call it — the ComfyUI path is direct and exclusive)
# ---------------------------------------------------------------------------
def _dispatch_to_plugin_provider(prompt: str, aspect_ratio: str):
    """Route to a registered plugin provider, or None if none selected."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        if isinstance(section, dict):
            provider = section.get("provider")
            if isinstance(provider, str) and provider.strip():
                provider = provider.strip()
            else:
                return None
        else:
            return None
    except Exception:
        return None

    if not provider or provider == "comfyui":
        return None

    try:
        from agent.image_gen_registry import get_provider
        from hermes_cli.plugins import _ensure_plugins_discovered
        _ensure_plugins_discovered()
        plug = get_provider(provider)
    except Exception as exc:
        logger.debug("image_gen plugin dispatch skipped: %s", exc)
        return None

    if plug is None:
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"image_gen.provider='{provider}' is set but no plugin registered that name.",
            "error_type": "provider_not_registered",
        })

    try:
        result = plug.generate(prompt=prompt, aspect_ratio=aspect_ratio)
    except Exception as exc:
        logger.warning("Image gen provider '%s' raised: %s", getattr(plug, "name", "?"), exc)
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"Provider '{getattr(plug, 'name', '?')}' error: {exc}",
            "error_type": "provider_exception",
        })

    if not isinstance(result, dict):
        return json.dumps({
            "success": False,
            "image": None,
            "error": "Provider returned a non-dict result",
            "error_type": "provider_contract",
        })
    return json.dumps(result)


# ---------------------------------------------------------------------------
# ComfyUI handler
# ---------------------------------------------------------------------------
def _handle_image_generate(args: dict[str, Any], **kw) -> str:
    """Generate an image via local ComfyUI (FluxedUp + Sydney LoRA).

    Uses ~/.hermes/scripts/comfyui_image_gen.py which POSTs to localhost:8188.
    Output image path is prefixed with MEDIA: so the gateway delivers it as a
    Telegram photo.
    """
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for image generation")
    aspect_ratio = args.get("aspect_ratio", DEFAULT_ASPECT_RATIO)

    # Try plugin dispatch first (e.g. a user registered a different backend)
    dispatch_result = _dispatch_to_plugin_provider(prompt, aspect_ratio)
    if dispatch_result is not None:
        return dispatch_result

    # Load ComfyUI backend — lives in ~/.hermes/scripts/, survives hermes updates
    try:
        import sys as _sys
        _script_path = str(Path.home() / ".hermes" / "scripts" / "comfyui_image_gen.py")
        if _script_path not in _sys.path:
            _sys.path.insert(0, str(Path(_script_path).parent))
        from comfyui_image_gen import generate_image as _comfy_generate
        from comfyui_image_gen import _check_comfyui as _check_comfy
    except Exception as _exc:
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"Failed to load ComfyUI backend: {_exc}",
            "error_type": "import_error",
        })

    # Health check
    if not _check_comfy():
        return json.dumps({
            "success": False,
            "image": None,
            "error": "ComfyUI server unreachable on port 8188. Make sure ComfyUI is running.",
            "error_type": "server_unreachable",
        })

    # Map aspect_ratio to ComfyUI dimension key
    _aspect_map = {
        "landscape": "landscape",
        "square": "square",
        "portrait": "portrait",
        "portrait_tall": "portrait_tall",
    }
    _aspect = _aspect_map.get(aspect_ratio, "portrait")

    # Retry: up to 2 attempts with 3s backoff for transient failures
    _result = None
    for _attempt in range(2):
        try:
            _result = _comfy_generate(prompt=prompt, aspect_ratio=_aspect)
        except Exception as _exc:
            _result = {"success": False, "image": None, "error": str(_exc)}

        if _result.get("success"):
            break

        if _attempt == 0:
            logger.warning(
                "ComfyUI generation attempt %d failed: %s — retrying",
                _attempt + 1, _result.get("error"),
            )
            time.sleep(3)
    else:
        return json.dumps({
            "success": False,
            "image": None,
            "error": f"ComfyUI generation failed after 2 attempts: {_result.get('error')}",
            "error_type": "generation_error",
        })

    if _result.get("success") and _result.get("image"):
        _result["image"] = "MEDIA:" + _result["image"]

    return json.dumps(_result)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
registry.register(
    name="image_generate",
    toolset="image_gen",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=_handle_image_generate,
    check_fn=check_image_generation_requirements,
    requires_env=[],
    is_async=False,
    emoji="🎨",
)
