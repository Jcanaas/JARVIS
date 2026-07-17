"""Unified Gemini SDK wrapper. Single source of truth for google.genai initialization."""

import json
from typing import Optional


def get_api_key() -> str:
    """Read Gemini API key from config."""
    from actions.paths import config_path
    config_file = config_path("api_keys.json")
    with open(config_file, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


_client = None


def get_client():
    """Get or initialize the google.genai client (singleton)."""
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=get_api_key())
    return _client


# Free-tier quota is tracked per model, not per project — when one model's
# daily allowance runs out, others (different families/tiers) usually still
# have headroom. Ordered cheapest/most-available first; skips whichever
# model was actually requested since that's tried first anyway.
_FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemma-4-26b-a4b-it",
]

# Models that recently answered 429 — skip them for a while instead of paying
# a failed roundtrip on every single call (free-tier quotas reset daily; a
# short cooldown just keeps latency down while exhausted).
_QUOTA_COOLDOWN_SECONDS = 600
_quota_exhausted_until: dict[str, float] = {}


class ModelWrapper:
    """Mimics the classic genai.GenerativeModel interface on top of google.genai."""

    def __init__(self, model_name: str, client, system_instruction: Optional[str] = None):
        self.model_name = model_name
        self.client = client
        self.system_instruction = system_instruction

    def generate_content(self, *args, **kwargs):
        import time as _time
        from google.genai import types
        from google.genai import errors as genai_errors

        contents = args[0] if args else kwargs.pop('contents', None)
        kwargs.pop('contents', None)
        kwargs.pop('config', None)  # no caller passes this today; always build fresh per attempt

        candidates = [self.model_name] + [m for m in _FALLBACK_MODELS if m != self.model_name]

        now = _time.time()
        available = [m for m in candidates if _quota_exhausted_until.get(m, 0) < now]
        if available:
            candidates = available

        last_error: Exception | None = None

        for i, model_name in enumerate(candidates):
            is_last = (i == len(candidates) - 1)

            config = types.GenerateContentConfig()
            if self.system_instruction:
                config.system_instruction = self.system_instruction
            # 2.5-series models "think" by default. Without an explicit budget
            # the response can come back as thought-only parts, so response.text
            # is an empty string ("" -> JSON parse failures downstream). None of
            # our callers want extended reasoning, just a fast structured reply.
            if "2.5" in model_name:
                config.thinking_config = types.ThinkingConfig(thinking_budget=0)

            try:
                response = self.client.models.generate_content(
                    model=model_name, contents=contents, config=config, **kwargs
                )
            except genai_errors.ClientError as e:
                last_error = e
                if getattr(e, "code", None) == 429:
                    _quota_exhausted_until[model_name] = _time.time() + _QUOTA_COOLDOWN_SECONDS
                    if not is_last:
                        print(f"[GenAI] ⚠️ {model_name} quota exhausted, falling back to next model")
                        continue
                raise

            if not (response.text or "").strip():
                if not is_last:
                    print(f"[GenAI] ⚠️ Empty response from {model_name}, falling back to next model")
                    last_error = RuntimeError(f"Empty response from {model_name}")
                    continue
                print(f"[GenAI] ⚠️ Empty response text from {model_name} "
                      f"(finish_reason may be MAX_TOKENS or safety-blocked)")

            if model_name != self.model_name:
                print(f"[GenAI] ✅ Served by fallback model: {model_name}")
            return response

        raise last_error


def get_model(model_name: str, system_instruction: Optional[str] = None):
    """Get a model wrapper that mimics the classic API (for compatibility).

    Args:
        model_name: Model identifier (e.g., "gemini-2.0-flash", "gemma-4-26b-a4b-it")
        system_instruction: Optional system prompt applied to all generate_content() calls

    Returns:
        A model wrapper object with generate_content() method.
    """
    client = get_client()
    return ModelWrapper(model_name, client, system_instruction=system_instruction)


def generate_content(model: str, prompt, **kwargs):
    """Generate content using the specified model.

    Args:
        model: Model identifier
        prompt: Text or list of content (text/image/video)
        **kwargs: Additional generation config (temperature, top_p, etc.)

    Returns:
        Response object with generated content
    """
    client = get_client()
    return client.models.generate_content(
        model=model,
        contents=prompt,
        **kwargs
    )
