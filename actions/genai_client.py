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


class ModelWrapper:
    """Mimics the classic genai.GenerativeModel interface on top of google.genai."""

    def __init__(self, model_name: str, client, system_instruction: Optional[str] = None):
        self.model_name = model_name
        self.client = client
        self.system_instruction = system_instruction

    def generate_content(self, *args, **kwargs):
        from google.genai import types

        contents = args[0] if args else kwargs.pop('contents', None)
        kwargs.pop('contents', None)

        config = kwargs.pop('config', None)
        if self.system_instruction and config is None:
            config = types.GenerateContentConfig(system_instruction=self.system_instruction)
        elif self.system_instruction and config is not None:
            config.system_instruction = self.system_instruction

        return self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
            **kwargs
        )


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
