import time

import pytest

from actions import web_search as ws


# ── helpers ──────────────────────────────────────────────────────────────────

LONG_TEXT = "x" * 100  # supera el umbral de 60 chars de _store


def test_format_news_includes_source_and_url():
    results = [
        {"title": "Titular uno", "snippet": "resumen breve", "url": "https://a.com/x", "source": "ACME"},
        {"title": "", "snippet": "sin titulo se descarta", "url": "https://b.com"},
    ]
    out = ws._format_news("tema", results)
    assert "Titular uno" in out
    assert "[ACME]" in out
    assert "https://a.com/x" in out
    assert "sin titulo se descarta" not in out


def test_format_news_empty():
    assert ws._format_news("tema", []) == "No news found for: tema"


# ── _news: paralelo first-wins ───────────────────────────────────────────────

def test_news_first_result_wins(monkeypatch):
    def slow_gemini(query):
        time.sleep(0.5)
        return "GEMINI " + LONG_TEXT

    def fast_ddg(query, max_results=8):
        return [{"title": "rapido " + LONG_TEXT, "snippet": "", "url": "https://n.com", "source": "N"}]

    monkeypatch.setattr(ws, "_gemini_search", slow_gemini)
    monkeypatch.setattr(ws, "_ddg_news", fast_ddg)

    out = ws._news("tema")
    assert "rapido" in out
    # el lento no pisa al rapido
    time.sleep(0.6)
    assert "GEMINI" not in out


def test_news_fallback_when_one_fails(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        ws, "_ddg_news",
        lambda q, max_results=8: [{"title": "unico " + LONG_TEXT, "snippet": "", "url": "https://n.com", "source": "N"}],
    )
    out = ws._news("tema")
    assert "unico" in out


def test_news_both_fail(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: [])
    out = ws._news("tema")
    assert out == "No news found for: tema"


# ── ruteo por modo ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,target", [
    ("news",     "_news"),
    ("research", "_research"),
    ("price",    "_price"),
    ("search",   "_search"),
])
def test_web_search_mode_routing(monkeypatch, mode, target):
    called = {}
    monkeypatch.setattr(ws, target, lambda q: (called.setdefault("q", q), "OK")[1])
    out = ws.web_search({"query": "algo", "mode": mode})
    assert out == "OK"
    assert called["q"] == "algo"


def test_web_search_items_force_compare(monkeypatch):
    monkeypatch.setattr(ws, "_compare", lambda items, aspect: f"CMP {items} {aspect}")
    out = ws.web_search({"query": "", "items": ["a", "b"], "aspect": "price", "mode": "search"})
    assert out == "CMP ['a', 'b'] price"


def test_web_search_empty_query():
    assert "search query" in ws.web_search({"query": ""}).lower()


# ── briefing helper: parseo de titulares ─────────────────────────────────────

class _FakePart:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        part = _FakePart(text)
        cand = type("C", (), {})()
        cand.content = type("K", (), {})()
        cand.content.parts = [part]
        self.candidates = [cand]


def test_gemini_headlines_parses_numbered_lines(monkeypatch):
    raw = (
        "Here are the headlines:\n"
        "1. Primer titular con enjundia suficiente\n"
        "2) Segundo titular igual de largo\n"
        "corto\n"
        "3- **Tercer titular con asteriscos delante**\n"
        "That's all!\n"
    )

    class _FakeModels:
        def generate_content(self, **kwargs):
            return _FakeResp(raw)

    class _FakeClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", _FakeClient)
    monkeypatch.setattr(ws, "_get_api_key", lambda: "test-key")

    headlines, raw_out = ws._gemini_headlines(5)
    assert len(headlines) == 3
    assert headlines[0] == "Primer titular con enjundia suficiente"
    assert headlines[1] == "Segundo titular igual de largo"
    assert headlines[2].startswith("Tercer titular")
    assert "Here are the headlines" in raw_out
