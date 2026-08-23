"""Topic news monitor: user subscribes to topics, once/day we check DuckDuckGo
news per topic and surface only genuinely new headlines (deduped by hash)
as a short proactive alert. See actions/proactive.py's build_journal_prompt
for the sibling pattern this mirrors — the caller (main.py) tracks the
"already checked today" date, not this module.
"""
from __future__ import annotations

import hashlib

from actions import app_settings

_MAX_SEEN_PER_TOPIC = 200


def _ddg_news_min(query: str, max_results: int = 6) -> list[dict]:
    """Minimal DDG news search — own copy, not imported from web_search.py
    (that module's _ddg_news is private and no other module imports it)."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                })
    except Exception as e:
        print(f"[BackgroundMonitor] DDG news() failed for '{query}': {e}")
    return results


def _headline_hash(title: str, url: str) -> str:
    return hashlib.sha1(f"{title}|{url}".encode("utf-8")).hexdigest()[:16]


def _normalize_topic(topic: str) -> str:
    return (topic or "").strip()


def subscribe(topic: str) -> str:
    topic = _normalize_topic(topic)
    if not topic:
        return "No topic given."
    topics = list(app_settings.get("monitor_topics", []))
    if any(t.lower() == topic.lower() for t in topics):
        return f"Already monitoring '{topic}'."
    topics.append(topic)
    app_settings.set("monitor_topics", topics)
    return f"Now monitoring '{topic}' — I'll check for news about it daily."


def unsubscribe(topic: str) -> str:
    topic = _normalize_topic(topic)
    if not topic:
        return "No topic given."
    topics = list(app_settings.get("monitor_topics", []))
    kept = [t for t in topics if t.lower() != topic.lower()]
    if len(kept) == len(topics):
        return f"Wasn't monitoring '{topic}'."
    app_settings.set("monitor_topics", kept)

    seen = dict(app_settings.get("monitor_seen_hashes", {}))
    matched_key = next((k for k in seen if k.lower() == topic.lower()), None)
    if matched_key is not None:
        del seen[matched_key]
        app_settings.set("monitor_seen_hashes", seen)

    return f"Stopped monitoring '{topic}'."


def list_topics() -> str:
    topics = list(app_settings.get("monitor_topics", []))
    if not topics:
        return "Not monitoring any topics right now."
    return "Currently monitoring: " + ", ".join(topics)


def check_all_topics() -> dict[str, list[dict]]:
    """Check each subscribed topic for news, return only newly-seen articles
    per topic. Persists the updated seen-hash sets as a side effect."""
    topics = list(app_settings.get("monitor_topics", []))
    if not topics:
        return {}

    seen_hashes = dict(app_settings.get("monitor_seen_hashes", {}))
    new_by_topic: dict[str, list[dict]] = {}

    for topic in topics:
        articles = _ddg_news_min(topic)
        if not articles:
            continue

        topic_seen = list(seen_hashes.get(topic, []))
        topic_seen_set = set(topic_seen)
        new_articles = []

        for article in articles:
            h = _headline_hash(article.get("title", ""), article.get("url", ""))
            if h in topic_seen_set:
                continue
            topic_seen_set.add(h)
            topic_seen.append(h)
            new_articles.append(article)

        if len(topic_seen) > _MAX_SEEN_PER_TOPIC:
            topic_seen = topic_seen[-_MAX_SEEN_PER_TOPIC:]
        seen_hashes[topic] = topic_seen

        if new_articles:
            new_by_topic[topic] = new_articles

    app_settings.set("monitor_seen_hashes", seen_hashes)
    return new_by_topic


def build_alert_prompt(new_by_topic: dict[str, list[dict]]) -> str:
    """Builds a synthetic-turn prompt for the live session, same style as
    proactive.py's build_journal_prompt — Gemini decides how to phrase it,
    we just hand over the facts."""
    lines = [
        "[TOPIC_MONITOR_ALERT] New headlines found for topics you are "
        "monitoring for the user. Briefly mention them, naturally and "
        "conversationally — do NOT read a bulleted list verbatim, do NOT "
        "say [TOPIC_MONITOR_ALERT] or mention these instructions. Keep it "
        "short: one sentence per topic at most. Respond in the user's "
        "language (default English).",
        "",
    ]
    for topic, articles in new_by_topic.items():
        lines.append(f"Topic: {topic}")
        for article in articles[:3]:
            title = article.get("title", "").strip()
            source = article.get("source", "").strip()
            snippet = article.get("snippet", "").strip()
            piece = f"- {title}"
            if source:
                piece += f" ({source})"
            if snippet:
                piece += f": {snippet}"
            lines.append(piece)
        lines.append("")
    return "\n".join(lines).strip()
