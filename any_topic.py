"""Play any topic: the rules for searching Wikipedia and accepting a topic (pure, no network).

On the setup page a player can type any topic instead of picking a category. Suggestions come from Wikipedia's
search as they type (data.suggest_topics), and before a quiz is made the topic must resolve to a real article
(data.resolve_topic): free text is never handed to the AI as a "topic", so every quiz still has a source.
A topic that resolves is then rated by the AI for its audience (children, teens, adults or never) and only played
at a difficulty whose age group fits (allowed): the quiz's difficulty already says who is playing.
This module reads what Wikipedia's API and the AI answer; the requests themselves are in data.py.
"""
import re

# The audience the AI gives a typed topic (data.check_topic_audience), youngest first; 'never' is never played
AUDIENCES = ('children', 'teens', 'adults', 'never')
# The youngest players each difficulty is for, as the highest audience it may play (the labels say the ages)
ALLOWED_UP_TO = {'Easy (Ages 7-12)': 'children', 'Medium (Ages 13-17)': 'teens', 'Hard (Ages 18-99)': 'adults'}

MIN_QUERY = 2      # Characters before suggestions are asked for
MAX_QUERY = 80
MAX_SUGGESTIONS = 8
ANY_TOPIC = 'topic'  # The setup form's category value for "Any topic"


def clean_query(text):
    """What the player typed, tidied: one line, single spaces, at most MAX_QUERY characters."""
    return re.sub(r'\s+', ' ', text or '').strip()[:MAX_QUERY]


def allowed(audience, difficulty):
    """Whether a topic the AI rated `audience` may be played at `difficulty`. Unknown ratings and difficulties are
    treated as the strictest case, so a surprise answer never lets a topic through."""
    if audience not in AUDIENCES or audience == 'never':
        return False
    limit = ALLOWED_UP_TO.get(difficulty, 'children')
    return AUDIENCES.index(audience) <= AUDIENCES.index(limit)


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
