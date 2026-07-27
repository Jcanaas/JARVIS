"""Real-time OCR + translation backend for the screen overlay.

No dedicated OCR engine (pytesseract/easyocr) is bundled — pytesseract needs
a separately installed Tesseract binary on PATH (can't assume it's there),
and easyocr ships its own multi-hundred-MB ML models. Gemini's vision model
already does OCR and translation in one call, so a single request covers
both instead of adding either heavy dependency.
"""
from __future__ import annotations

_VISION_MODEL = "gemini-2.5-flash-lite"


def translate_screen_text(target_lang: str = "es") -> str:
    """OCR whatever text is visible on screen right now and translate it.

    Returns the translated text (one line per detected text line/block, in
    the original top-to-bottom order), or "" if no text was found or the
    lookup failed. Best-effort: this backs a live overlay, never worth
    raising an exception over.
    """
    import io
    import PIL.Image
    from actions.screen_processor import _capture_screen
    from actions.genai_client import get_model

    try:
        image_bytes, _mime = _capture_screen()
        img = PIL.Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        print(f"[ScreenTranslate] ⚠️ capture error: {e}")
        return ""

    prompt = (
        f"OCR every piece of readable text visible in this screenshot, then translate it to "
        f"{target_lang}. Return ONLY the translated text, one line per original text line/block, "
        "in the same order they appear top-to-bottom. If there is no readable text, return an "
        "empty response. No explanations, no labels, no quotes, no markdown."
    )
    try:
        model = get_model(_VISION_MODEL)
        response = model.generate_content([prompt, img])
        return str(getattr(response, "text", "") or "").strip()
    except Exception as e:
        print(f"[ScreenTranslate] ⚠️ translate error: {e}")
        return ""
