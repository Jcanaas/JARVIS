"""One-off LLM text transforms for the clipboard-intelligence popup
(Ctrl+Alt+C). Same pattern as actions/screen_translator.py's
translate_screen_text: a small prompt + a single generate_content call,
fail-soft (never raises — this backs a live UI popup).
"""
from __future__ import annotations

_MODEL = "gemini-2.5-flash-lite"


def _run(prompt: str, text: str) -> str:
    from actions.genai_client import get_model

    try:
        model = get_model(_MODEL)
        response = model.generate_content([prompt, text])
        return str(getattr(response, "text", "") or "").strip()
    except Exception as e:
        print(f"[ClipboardIntel] ⚠️ {e}")
        return ""


def summarize(text: str) -> str:
    prompt = (
        "Summarize the following text in a few short sentences. "
        "Return ONLY the summary, no labels, no markdown."
    )
    return _run(prompt, text)


def translate(text: str, target_lang: str = "es") -> str:
    prompt = (
        f"Translate the following text to {target_lang}. "
        "Return ONLY the translated text, no labels, no markdown."
    )
    return _run(prompt, text)


def explain(text: str) -> str:
    prompt = (
        "Explain the following text clearly and concisely, as if to someone "
        "unfamiliar with the topic. Return ONLY the explanation, no labels, no markdown."
    )
    return _run(prompt, text)


def fix(text: str) -> str:
    prompt = (
        "Fix grammar, spelling, and punctuation in the following text. "
        "Preserve the original meaning, language, and tone. "
        "Return ONLY the corrected text, no labels, no markdown."
    )
    return _run(prompt, text)
