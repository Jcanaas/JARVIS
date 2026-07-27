import time

from actions.proactive import ProactiveEngine


def test_no_trigger_before_min_silence(monkeypatch):
    eng = ProactiveEngine(min_silence_secs=900, check_cooldown=600)
    now = time.monotonic()
    # usuario habló hace 100 s — aún no
    assert eng.should_trigger(now - 100) is False


def test_trigger_after_silence():
    eng = ProactiveEngine(min_silence_secs=900, check_cooldown=600)
    now = time.monotonic()
    assert eng.should_trigger(now - 901) is True


def test_cooldown_between_triggers():
    eng = ProactiveEngine(min_silence_secs=900, check_cooldown=600)
    now = time.monotonic()
    assert eng.should_trigger(now - 901) is True
    eng.mark_triggered()
    # sigue en silencio, pero el cooldown manda
    assert eng.should_trigger(now - 2000) is False


def test_build_prompt_contains_context(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "projects: JARVIS fork")
    eng = ProactiveEngine()
    prompt = eng.build_prompt({"identity": {}})
    assert "[PROACTIVE_CHECK]" in prompt
    assert "projects: JARVIS fork" in prompt
    assert "1-3 sentences" in prompt


def test_build_prompt_contains_custom_user_instructions(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "")
    eng = ProactiveEngine()
    prompt = eng.build_prompt({}, "Recuérdame hacer una pausa y beber agua.")
    assert "User instructions for proactive mode:" in prompt
    assert "Recuérdame hacer una pausa y beber agua." in prompt


def test_build_prompt_omits_empty_custom_instructions(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "")
    prompt = ProactiveEngine().build_prompt({}, "   ")
    assert "User instructions for proactive mode:" not in prompt
