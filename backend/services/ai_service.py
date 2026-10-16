import json
import logging
import os
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import requests
from google import genai

from backend.config import logger

KNOWN_FLASH_MODELS = [
    'gemini-flash-latest',
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-2.0-flash',
    'gemini-2.0-flash-lite',
    'gemini-1.5-flash',
    'gemini-1.5-flash-8b',
]

# ── AI Provider Abstraction ─────────────────────────────────────────────────
# Cheat Clip Pro can analyse videos with either Google Gemini (native SDK) or
# Moonshot AI's Kimi models (OpenAI-compatible Chat Completions endpoint).
# The provider is chosen from the requested model name and, failing that, from
# the shape of the supplied API key.

PROVIDER_GEMINI = "gemini"
PROVIDER_KIMI = "kimi"

# Moonshot keys are prefixed `sk-`; Google keys are prefixed `AIza`. The
# prefixes are distinctive enough to auto-route a pasted key when the caller
# did not pin a provider explicitly.
KIMI_KEY_PREFIX = "sk-"
GEMINI_KEY_PREFIX = "AIza"

DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"

# Kimi model ids, newest/most capable first. `kimi-k3` is the current flagship
# (1M context, always-on reasoning, OpenAI-compatible JSON mode).
KNOWN_KIMI_MODELS = [
    'kimi-k3',
    'kimi-k3-256k',
    'kimi-k2.7',
    'kimi-k2.7-code',
    'kimi-k2.6',
    'kimi-k2.5',
]


def get_kimi_base_url() -> str:
    """Base URL for the Kimi/Moonshot OpenAI-compatible API.

    Overridable via ``MOONSHOT_BASE_URL`` so users can route through a proxy or
    an aggregator that speaks the same OpenAI Chat Completions contract.
    """
    base = (os.environ.get("MOONSHOT_BASE_URL") or DEFAULT_KIMI_BASE_URL).strip()
    return base.rstrip("/")


def is_kimi_model(model: str) -> bool:
    """Whether a model identifier belongs to the Kimi/Moonshot family."""
    name = (model or "").strip().lower()
    return "kimi" in name or "moonshot" in name


def is_kimi_api_key(api_key: str) -> bool:
    return (api_key or "").strip().startswith(KIMI_KEY_PREFIX)


def detect_provider(model: str = "", api_key: str = "") -> str:
    """Resolves which AI provider a request should use.

    An explicit Kimi model name always wins, so a user who picks `kimi-k3` is
    never silently sent to Gemini. Otherwise the key prefix decides.
    """
    if is_kimi_model(model):
        return PROVIDER_KIMI
    if is_kimi_api_key(api_key):
        return PROVIDER_KIMI
    return PROVIDER_GEMINI


def get_kimi_api_key(explicit_key: str = "") -> str:
    """Resolves the Kimi key from the request, falling back to the environment."""
    return (
        (explicit_key or "").strip()
        or (os.environ.get("MOONSHOT_API_KEY") or "").strip()
        or (os.environ.get("KIMI_API_KEY") or "").strip()
    )


def build_kimi_model_chain(requested_model: str) -> List[str]:
    """Fallback chain for Kimi models, requested model first, capped to 4."""
    chain = [(requested_model or "kimi-k3").strip()]
    for known in KNOWN_KIMI_MODELS:
        if known not in chain:
            chain.append(known)
    return chain[:4]


def list_available_kimi_models(api_key: str = "") -> List[str]:
    """Lists Kimi models for a key via the OpenAI-compatible /models endpoint.

    Falls back to the bundled known-model list when the key is absent, invalid,
    or the endpoint is unreachable, so the UI always has something to show.
    """
    key = get_kimi_api_key(api_key)
    if not key or key.lower() == "mock" or not is_kimi_api_key(key):
        return list(KNOWN_KIMI_MODELS)

    try:
        resp = requests.get(
            f"{get_kimi_base_url()}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"Kimi model listing returned HTTP {resp.status_code}; using known models.")
            return list(KNOWN_KIMI_MODELS)

        payload = resp.json()
        discovered: List[str] = []
        for entry in payload.get("data") or []:
            model_id = str(entry.get("id") or "").strip()
            if not model_id:
                continue
            # Only surface chat-capable Kimi models, skipping embedding/vision-only ids.
            lowered = model_id.lower()
            if any(tok in lowered for tok in ("embed", "vision", "rerank", "tts", "audio")):
                continue
            if model_id not in discovered:
                discovered.append(model_id)

        known_first = [m for m in KNOWN_KIMI_MODELS if m in discovered]
        extras = [m for m in discovered if m not in known_first]
        ordered = known_first + extras
        return ordered or list(KNOWN_KIMI_MODELS)
    except Exception as e:
        logger.warning(f"Could not list Kimi models ({e}); using known models.")
        return list(KNOWN_KIMI_MODELS)


def format_kimi_request_error(status_code: int, body: str) -> str:
    """Turns a Kimi HTTP error into an actionable, user-facing message."""
    snippet = (body or "").strip()[:400]
    if status_code in (401, 403):
        return (
            "Moonshot rejected the Kimi API key (HTTP "
            f"{status_code}). Verify the key or create a new one at platform.kimi.ai. Details: {snippet}"
        )
    if status_code == 404:
        return (
            "The requested Kimi model is not available for this key. "
            f"Pick a different model such as kimi-k3. Details: {snippet}"
        )
    if status_code == 429:
        return f"Kimi quota or rate limit reached (HTTP 429). Wait briefly or use a fresh key. Details: {snippet}"
    if status_code >= 500:
        return f"Moonshot servers are temporarily unavailable (HTTP {status_code}). Details: {snippet}"
    return f"Kimi request failed (HTTP {status_code}). Details: {snippet}"


def kimi_chat_completion(
    api_key: str,
    model: str,
    prompt: str,
    json_schema: Optional[Dict[str, Any]] = None,
    timeout: float = 180.0,
    temperature: float = 0.2,
) -> SimpleNamespace:
    """Runs one Kimi Chat Completions call and returns a Gemini-shaped result.

    The returned object exposes ``.text`` (raw assistant content) and
    ``.parsed`` (the decoded dict when the response was valid JSON). Callers in
    the analyze pipeline can therefore treat Gemini and Kimi responses
    identically, and the existing lenient JSON repair logic is reused.
    """
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("A Moonshot/Kimi API key is required to use Kimi models.")

    messages = [{"role": "user", "content": prompt}]
    if json_schema is not None:
        # Kimi's json_object mode needs the word "json" in the prompt and a
        # described shape; it does not enforce a schema server-side.
        messages[0]["content"] = (
            f"{prompt}\n\n"
            "Respond with a single json object only, no markdown fences and no prose. "
            "It must match this json schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_schema is not None:
        payload["response_format"] = {"type": "json_object"}

    resp = requests.post(
        f"{get_kimi_base_url()}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(format_kimi_request_error(resp.status_code, resp.text))

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Kimi returned a non-JSON response: {e}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Kimi returned no completion choices. Response: {str(data)[:300]}")

    content = (choices[0].get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise RuntimeError("Kimi returned an empty completion.")

    parsed = None
    try:
        decoded = json.loads(content)
        if isinstance(decoded, dict):
            parsed = decoded
    except Exception:
        # Leave parsed as None so the pipeline's lenient repair parser runs.
        pass

    return SimpleNamespace(parsed=parsed, text=content, model=model)


def parse_gemini_model_sort_key(name: str):
    """Sort key for Gemini models: parses major and minor versions (e.g. 3.7, 3.6, 3.5, 2.5, 2.0, 1.5),
    tier (standard > lite/8b > preview/exp), so newest and most capable models come first."""
    name_clean = (name or "").split('/')[-1].lower()
    m = re.search(r'(\d+)(?:\.(\d+))?', name_clean)
    if m:
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) is not None else 0
    else:
        major, minor = 0, 0

    if 'lite' in name_clean or '8b' in name_clean:
        tier = 2
    elif 'exp' in name_clean or 'preview' in name_clean:
        tier = 1
    else:
        tier = 3

    return (major, minor, tier, name_clean)


def get_flash_models_for_key(client: genai.Client) -> List[str]:
    """Dynamically query all available flash models for the given API key.
    Discovers newer versions (e.g., 3.7, 3.6, 3.5) and earlier versions (2.5, 2.0, 1.5),
    merging with known fallback models and sorting in descending order of version/capability."""
    discovered = []
    try:
        models_page = client.models.list()
        for m in models_page:
            name = m.name or ""
            short_name = name.split('/')[-1]
            if "gemini" in short_name.lower() and "flash" in short_name.lower():
                if m.supported_actions and "generateContent" not in m.supported_actions:
                    continue
                # Exclude non-text, specialized, or non-generative tasks
                exclude_keywords = [
                    'tuning', 'thinking', 'vision', 'image', 'tts',
                    'omni', 'customtools', 'embed', 'realtime', 'robotics'
                ]
                if not any(x in short_name.lower() for x in exclude_keywords):
                    if short_name not in discovered:
                        discovered.append(short_name)
    except Exception as e:
        logger.warning(f"Could not dynamically list models: {e}")

    # Combine discovered with known flash models, preserving uniqueness
    combined_pool = list(dict.fromkeys(discovered + KNOWN_FLASH_MODELS))
    # Sort descending so newest versions (e.g. 2.5, 2.0, 1.5) are prioritized
    ordered = sorted(combined_pool, key=parse_gemini_model_sort_key, reverse=True)
    # Cap to top 4 distinct models to prevent excessive fallback cycles
    return ordered[:4]


def list_available_gemini_models(api_key: str = "") -> List[str]:
    """Fetches list of available Gemini models using the user's API key, prioritizing Flash models (newest first)."""
    default_models = [
        'gemini-2.5-flash',
        'gemini-2.5-flash-lite',
        'gemini-2.0-flash',
        'gemini-2.0-flash-lite',
        'gemini-1.5-flash',
        'gemini-2.5-pro'
    ]
    key_to_use = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key_to_use or key_to_use.lower() == "mock":
        return default_models
    try:
        client = genai.Client(api_key=key_to_use)
        models_page = client.models.list()
        
        flash_models = []
        pro_models = []
        other_models = []
        
        for m in models_page:
            name = m.name or ""
            if "gemini" in name.lower():
                if m.supported_actions and "generateContent" not in m.supported_actions:
                    continue
                
                short_name = name.split('/')[-1]
                exclude_keywords = [
                    'tuning', 'thinking', 'vision', 'image', 'tts',
                    'omni', 'customtools', 'embed', 'realtime', 'robotics'
                ]
                if any(x in short_name.lower() for x in exclude_keywords):
                    continue
                
                if "flash" in short_name.lower():
                    if short_name not in flash_models:
                        flash_models.append(short_name)
                elif "pro" in short_name.lower():
                    if short_name not in pro_models:
                        pro_models.append(short_name)
                elif any(x in short_name.lower() for x in ['lite', 'exp']):
                    if short_name not in other_models:
                        other_models.append(short_name)
        
        # Sort flash models by version descending (e.g. 3.7, 3.6, 3.5, 2.5, 2.0, 1.5)
        ordered_flash = sorted(
            list(dict.fromkeys(flash_models + KNOWN_FLASH_MODELS)),
            key=parse_gemini_model_sort_key,
            reverse=True
        )
        ordered_pro = sorted(pro_models, key=parse_gemini_model_sort_key, reverse=True)
        ordered_other = sorted(other_models, key=parse_gemini_model_sort_key, reverse=True)
        
        final_list = ordered_flash + ordered_pro + ordered_other
        if not final_list:
            final_list = default_models
            
        return final_list
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return default_models


# ── Text-to-Video Prompt Generation ─────────────────────────────────────────
# Turns an analysed video (transcript + metadata) into a reusable text-to-video
# generation prompt. The prompt is deliberately written to be portable across
# generators (Veo, Sora, Runway, Kling, ...), so it avoids vendor-specific syntax.

PROMPT_SYSTEM_RULES = (
    "You are a senior prompt engineer for AI text-to-video models "
    "(Veo, Sora, Runway, Kling, Pika, Luma). You receive a transcript and metadata from an "
    "existing video and must describe how to RE-CREATE it from scratch as a generated video.\n"
    "\n"
    "Rules:\n"
    "- Describe only what a camera and a scene can show: subject, wardrobe, action, setting, "
    "lighting, lens, camera movement, pacing, and audio.\n"
    "- Never mention that the source was a video, a transcript, a link, or a speaker's name.\n"
    "- Keep the same language as the transcript for `prompt` and `shot_list`, but write the "
    "technical fields (style, camera, lighting, audio) in English, which video models parse best.\n"
    "- Be concrete and visual. Replace abstractions with filmable detail.\n"
    "- Do not invent brand names, real people, or copyrighted characters."
)


def build_video_prompt_schema() -> Dict[str, Any]:
    """JSON schema for the structured text-to-video prompt."""
    return {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The complete, ready-to-paste text-to-video prompt (same language as the transcript).",
            },
            "summary": {
                "type": "string",
                "description": "One or two sentences describing what the generated video shows.",
            },
            "style": {"type": "string", "description": "Visual style, e.g. cinematic realism, documentary, anime."},
            "subject": {"type": "string", "description": "The main subject(s) and their appearance/wardrobe."},
            "action": {"type": "string", "description": "What happens on screen, in order."},
            "setting": {"type": "string", "description": "Location, era, time of day, environment detail."},
            "camera": {"type": "string", "description": "Shot size, lens, angle and camera movement."},
            "lighting": {"type": "string", "description": "Lighting design, colour palette and mood."},
            "audio": {"type": "string", "description": "Ambient sound, music and any spoken lines."},
            "negative_prompt": {"type": "string", "description": "Artifacts or content to avoid."},
            "shot_list": {
                "type": "array",
                "description": "Ordered shot-by-shot breakdown, each with a time range and description.",
                "items": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string", "description": "Time range, e.g. '0-5s'."},
                        "description": {"type": "string", "description": "What the camera shows in this shot."},
                    },
                    "required": ["time", "description"],
                },
            },
        },
        "required": ["prompt", "summary"],
    }


def _build_video_prompt_request(
    transcript_text: str,
    title: str = "",
    channel: str = "",
    duration: float = 0.0,
    style_hint: str = "",
    extra_instructions: str = "",
) -> str:
    """Assembles the analysis request sent to the language model."""
    meta_lines = [f"Source title: {title or 'unknown'}"]
    if channel:
        meta_lines.append(f"Source channel/author: {channel}")
    if duration:
        meta_lines.append(f"Source duration: {round(duration, 1)} seconds")
    if style_hint:
        meta_lines.append(f"Requested visual style: {style_hint}")
    if extra_instructions:
        meta_lines.append(f"Additional user instructions: {extra_instructions}")

    return (
        f"{PROMPT_SYSTEM_RULES}\n\n"
        "=== SOURCE METADATA ===\n"
        + "\n".join(meta_lines)
        + "\n\n=== SOURCE TRANSCRIPT ===\n"
        + (transcript_text or "(no transcript available — infer a plausible scene from the metadata)")
        + "\n\n=== TASK ===\n"
        "Produce a structured text-to-video prompt that recreates the substance of this video. "
        "Cover the whole narrative, not just the opening, and give the shot list the same number "
        "of beats as the source so the pacing is preserved. Respond with a single json object only."
    )


def generate_video_prompt(
    api_key: str,
    transcript_text: str,
    model: str = "",
    title: str = "",
    channel: str = "",
    duration: float = 0.0,
    style_hint: str = "",
    extra_instructions: str = "",
) -> SimpleNamespace:
    """Turns a transcript into a structured text-to-video prompt.

    Routes through whichever provider the key/model selects, mirroring the
    analyze pipeline, and returns a Gemini-shaped result exposing ``.text`` and
    ``.parsed`` so callers parse both providers identically.
    """
    request_text = _build_video_prompt_request(
        transcript_text=transcript_text,
        title=title,
        channel=channel,
        duration=duration,
        style_hint=style_hint,
        extra_instructions=extra_instructions,
    )
    schema = build_video_prompt_schema()
    provider = detect_provider(model=model, api_key=api_key)

    if provider == PROVIDER_KIMI:
        kimi_model = (model or "").strip() if is_kimi_model(model) else "kimi-k3"
        return kimi_chat_completion(
            api_key=get_kimi_api_key(api_key),
            model=kimi_model,
            prompt=request_text,
            json_schema=schema,
            timeout=180.0,
            temperature=0.4,
        )

    gemini_model = (model or "").strip() or "gemini-2.5-flash"
    if is_kimi_model(gemini_model):
        gemini_model = "gemini-2.5-flash"

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=gemini_model,
        contents=request_text,
        config={
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": 0.4,
        },
    )
    text = getattr(response, "text", "") or ""
    parsed = None
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            parsed = decoded
    except Exception:
        pass
    return SimpleNamespace(parsed=parsed, text=text, model=gemini_model)
