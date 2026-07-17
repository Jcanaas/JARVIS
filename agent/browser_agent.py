"""
Reactive browser agent: observe -> decide -> act loop for multi-step web
tasks (posting on social media, filling and submitting forms, buying
something, logging in and doing something). The planner produces a plan
with blind clicks generated before ever seeing the page; this module
replaces that for anything that needs to react to what's actually on
screen.
"""

import json
import re
import threading
import time
from typing import Callable, Optional

BROWSER_AGENT_PROMPT = """You are the browser-automation module of MARK XXV, a personal AI assistant.

You control a real, already-open browser tab. On each turn you are given:
- The goal.
- The history of actions you've already taken this run and their results.
- The current page state: URL, title, the interactive elements visible on
  the page (role + accessible name), and a slice of visible text.

Decide the SINGLE next action that moves toward the goal.

AVAILABLE ACTIONS (args shown per action):
click_ref    {"ref": "e42"}   clicks the element with [ref=e42] from the page state.
                              THE PREFERRED WAY TO CLICK — always use the ref when one is shown.
type_ref     {"ref": "e42", "text": "..."}   clicks then types into the element with that ref.
                              THE PREFERRED WAY TO TYPE.
go_to        {"url": "..."}
search       {"query": "...", "engine": "google|bing|duckduckgo|yandex"}   optional engine
smart_click  {"description": "..."}   fallback when no ref is available: clicks the element
                                       whose accessible name/label CONTAINS description
                                       (case-insensitive substring).
smart_type   {"description": "...", "text": "..."}   fallback when no ref is available.
click        {"text": "..."} or {"selector": "..."}
type         {"selector": "...", "text": "..."}
press        {"key": "Enter|Escape|Tab|..."}
scroll       {"direction": "up|down", "amount": 500}
fill_form    {"fields": {"css selector": "value", ...}}
back         {}
forward      {}
reload       {}
new_tab      {"url": "..."}   optional
wait         {"seconds": 1}   max 5, use sparingly to let something finish loading/rendering
screenshot   {}
finish       {"success": true|false, "summary": "..."}

RULES:
- The page state lists elements as `role "accessible name" [ref=eN]`. When the element you
  need has a ref, ALWAYS act on it with click_ref/type_ref — never re-guess it by name.
  Refs are only valid for the CURRENT state; after any action you get a fresh state with
  fresh refs.
- Accessible names appear in the PAGE'S OWN LANGUAGE (often Spanish, e.g. "Crear publicación",
  "Publicar", "Buscar"). Read them from the state — NEVER translate or invent a name.
- If the element you need is not in the state: scroll ONCE and re-check. Do not scroll
  repeatedly, do not click navigation items (Inicio/Home, logo, tabs) hoping it appears —
  if two consecutive states still don't show it, try a direct URL for the flow instead
  (e.g. for posting on LinkedIn: https://www.linkedin.com/feed/?shareActive=true opens the
  post composer directly).
- Only call finish(success=true) once the CURRENT state gives real confirmation the goal is
  done (a success message, the posted/submitted content itself visible, an order/post
  confirmation, etc). Clicking a "Post"/"Submit" button is not by itself confirmation — check
  the next state before declaring success.
- Call finish(success=false, summary=...) if the task is genuinely blocked (login wall you
  cannot pass, CAPTCHA, missing information only the user can provide) — explain what's
  blocking it.
- When the state says a dialog is focused, work INSIDE it: composer dialogs (e.g. LinkedIn's
  "Crear publicación") have a textbox — type_ref your text into it FIRST. A button marked
  [disabled] becomes enabled after its required input is filled; type first, then click it.
- If an action's result was an error, do not repeat the exact same action — pick a different
  ref, scroll once, or navigate directly.
- One action per turn. Never output more than one action.
- SAVED_PROFILE (if given below) holds the user's own non-sensitive info (name, email,
  phone, address). When a form field's label/placeholder matches one of those, fill it with
  type_ref using the saved value instead of asking or guessing. NEVER type a value for a
  password, card number, CVV, expiry, or ID/passport field even if SAVED_PROFILE has one —
  those are always typed by the user directly, skip that field and continue.

Return ONLY valid JSON, no markdown, no explanation outside the JSON:
{"action": "...", "args": {...}, "reasoning": "short reason", "done": false}
"""

# Only these fields are ever read from settings and handed to the model —
# anything else in form_profile (nothing sensitive should be there, but this
# is the hard boundary) is ignored.
_PROFILE_ALLOWED_KEYS = ("name", "email", "phone", "address", "city", "postal_code", "country")


def _format_profile_context() -> str:
    from actions import app_settings

    profile = app_settings.get("form_profile", {}) or {}
    if not isinstance(profile, dict):
        return ""
    safe = {k: v for k, v in profile.items() if k in _PROFILE_ALLOWED_KEYS and str(v or "").strip()}
    if not safe:
        return ""
    lines = "\n".join(f"  {k}: {v}" for k, v in safe.items())
    return f"\nSAVED_PROFILE (autofill matching fields only, see rules above):\n{lines}\n"

_ACTION_ARG_BUILDERS = {
    "click_ref":   lambda a: {"ref": a.get("ref", "")},
    "type_ref":    lambda a: {"ref": a.get("ref", ""), "text": a.get("text", "")},
    "go_to":       lambda a: {"url": a.get("url", "")},
    "search":      lambda a: {"query": a.get("query", ""), "engine": a.get("engine", "google")},
    "smart_click": lambda a: {"description": a.get("description", "")},
    "smart_type":  lambda a: {"description": a.get("description", ""), "text": a.get("text", "")},
    "click":       lambda a: {"text": a.get("text"), "selector": a.get("selector")},
    "type":        lambda a: {"selector": a.get("selector"), "text": a.get("text", "")},
    "press":       lambda a: {"key": a.get("key", "Enter")},
    "scroll":      lambda a: {"direction": a.get("direction", "down"), "amount": a.get("amount", 500)},
    "fill_form":   lambda a: {"fields": a.get("fields", {})},
    "back":        lambda a: {},
    "forward":     lambda a: {},
    "reload":      lambda a: {},
    "new_tab":     lambda a: {"url": a.get("url", "")},
    "screenshot":  lambda a: {},
}


def _to_browser_control_params(action: str, args: dict, browser: Optional[str]) -> Optional[dict]:
    builder = _ACTION_ARG_BUILDERS.get(action)
    if builder is None:
        return None
    params = {"action": action, "browser": browser}
    params.update(builder(args))
    return params


def run_browser_goal(
    goal:        str,
    browser:     Optional[str]          = None,
    speak:       Callable | None        = None,
    cancel_flag: threading.Event | None = None,
    max_steps:   int                    = 25,
    investigate: bool                   = False,
) -> str:
    """
    Runs an observe -> decide -> act loop against the live browser session
    until the goal is confirmed done, the model gives up, or max_steps is
    hit. Returns a plain success summary, or "ERROR: ..." on failure so
    callers can treat it the same way as browser_control failures.

    investigate: when True, saves a screenshot after every step and reports
    progress + the step's reasoning via event_bus, so the run is auditable
    step-by-step instead of a single opaque final result.
    """
    from actions.browser_control import browser_control
    from actions.genai_client import get_model

    checkpoint_dir = None
    if investigate:
        from actions.paths import DATA_DIR
        from actions import event_bus
        checkpoint_dir = DATA_DIR / "browser_checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

    profile_context = _format_profile_context()
    model = get_model(
        "gemini-2.5-flash",
        system_instruction=BROWSER_AGENT_PROMPT + profile_context,
    )

    def _observe() -> str:
        state = browser_control(parameters={"action": "get_state", "browser": browser})
        if isinstance(state, str) and state.startswith("ERROR:"):
            time.sleep(2)
            state = browser_control(parameters={"action": "get_state", "browser": browser})
        return state

    history: list[dict] = []

    # Don't launch the browser just to observe a blank page: if no session is
    # open yet, the obvious first action is go_to and the model can decide it
    # without a state. Launching happens during that first action instead,
    # shaving the slowest part off the critical path.
    from actions.browser_control import _registry as _bc_registry
    if _bc_registry._sessions:
        state = _observe()
    else:
        state = "(no browser open yet — nothing to observe; start by navigating with go_to)"

    MAX_CONSECUTIVE_PARSE_FAILURES = 3
    MAX_CONSECUTIVE_REPEATS        = 3

    consecutive_parse_failures = 0
    last_action_signature: tuple | None = None
    repeat_streak = 0

    for step_i in range(1, max_steps + 1):
        if cancel_flag and cancel_flag.is_set():
            return "ERROR: Browser goal cancelled."

        history_text = "\n".join(
            f"{i + 1}. {h['action']}({h['args']}) -> {h['result'][:200]}"
            for i, h in enumerate(history[-8:])
        ) or "(none yet)"

        prompt = (
            f'Goal: "{goal}"\n\n'
            f"Actions taken so far:\n{history_text}\n\n"
            f"Current page state:\n{state[:8000]}\n\n"
            f"Step {step_i} of max {max_steps}. Decide the next action toward the goal above."
        )

        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
            decision = json.loads(text)
            consecutive_parse_failures = 0
        except Exception as e:
            consecutive_parse_failures += 1
            print(f"[BrowserAgent] ⚠️ Decision parse failed "
                  f"({consecutive_parse_failures}/{MAX_CONSECUTIVE_PARSE_FAILURES}): {e}")
            if consecutive_parse_failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
                return (
                    "ERROR: The decision model stopped producing usable output "
                    f"after {consecutive_parse_failures} consecutive failed attempts."
                )
            # Re-observe before retrying — the previous attempt did nothing to
            # the page, but re-sending the exact same prompt unchanged just
            # invites the same failure again.
            state = _observe()
            continue

        action    = str(decision.get("action", "")).lower().strip()
        args      = decision.get("args", {}) or {}
        reasoning = decision.get("reasoning", "")
        print(f"[BrowserAgent] Step {step_i}: {action}({args}) — {reasoning}")

        if speak and step_i == 1:
            speak("Working on it in the browser, sir.")

        if action == "finish":
            success = bool(args.get("success"))
            summary = str(args.get("summary", "")).strip()
            if success:
                return summary or "Goal accomplished."
            return f"ERROR: {summary or 'Could not accomplish the goal.'}"

        signature = (action, json.dumps(args, sort_keys=True, default=str))
        if signature == last_action_signature:
            repeat_streak += 1
        else:
            repeat_streak = 1
            last_action_signature = signature
        if repeat_streak >= MAX_CONSECUTIVE_REPEATS:
            return (
                f"ERROR: Stuck repeating the same action ({action}({args})) "
                f"{repeat_streak} times in a row without progress toward the goal."
            )

        if action == "wait":
            seconds = min(float(args.get("seconds", 1) or 1), 5)
            time.sleep(seconds)
            result = f"Waited {seconds}s."
        else:
            bc_params = _to_browser_control_params(action, args, browser)
            if bc_params is None:
                result = f"ERROR: Unknown action '{action}'."
            else:
                result = browser_control(parameters=bc_params)

        history.append({"action": action, "args": args, "result": str(result)})

        if checkpoint_dir is not None:
            shot_path = str(checkpoint_dir / f"step_{step_i:02d}.png")
            browser_control(parameters={"action": "screenshot", "browser": browser, "path": shot_path})
            event_bus.progress(max_steps, step_i, label=f"{action}: {reasoning}"[:80])
            event_bus.log("BrowserAgent", f"Paso {step_i}: {action} — {reasoning} [{shot_path}]")

        state = _observe()

    return f"ERROR: Reached max steps ({max_steps}) without confirming the goal was accomplished."
