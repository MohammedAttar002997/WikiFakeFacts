"""Play any topic: the rules for searching Wikipedia and accepting a topic (pure, no network).

On the setup page a player can type any topic instead of picking a category. Suggestions come from Wikipedia's
search as they type (data.suggest_topics), and before a quiz is made the topic must resolve to a real article
(data.resolve_topic): free text is never handed to the AI as a "topic", so every quiz still has a source.
This module reads what Wikipedia's API answers; the requests themselves are in data.py.
"""
import re

MIN_QUERY = 2      # Characters before suggestions are asked for
MAX_QUERY = 80
MAX_SUGGESTIONS = 8
ANY_TOPIC = 'topic'  # The setup form's category value for "Any topic"


def clean_query(text):
    """What the player typed, tidied: one line, single spaces, at most MAX_QUERY characters."""
    return re.sub(r'\s+', ' ', text or '').strip()[:MAX_QUERY]


def suggestions(answer):
    """Titles from an opensearch answer ([query, [titles], [descriptions], [urls]]); [] for anything else."""
    if not isinstance(answer, list) or len(answer) < 2 or not isinstance(answer[1], list):
        return []
    return [title for title in answer[1] if isinstance(title, str) and title][:MAX_SUGGESTIONS]


def resolved_title(answer):
    """The article's own title from a `query` answer for one title (formatversion=2, redirects followed, pageprops
    asked for), or None when there's no such article or it's a disambiguation page (a list of meanings, not a topic)."""
    try:
        page = answer['query']['pages'][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(page, dict) or page.get('missing') or page.get('invalid') or 'title' not in page:
        return None
    if page.get('ns', 0) != 0 or 'disambiguation' in (page.get('pageprops') or {}):
        return None
    return page['title']
