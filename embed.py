"""Rules for the embeddable round of the day (views/embed.py): a widget other sites put in an iframe.

The round comes from the latest past daily challenge, never today's (that would spoil the challenge), so it changes
once a day and costs no AI call. The widget keeps no state: the answer is a plain link carrying the daily's id, the
round's index and the picked option's position, so it works in iframes where browsers block cookies.
"""
import hashlib
import random
from html import escape

from topic_pages import playable_rounds

THEMES = ('auto', 'light', 'dark')
LOOKBACK = 7      # Past daily challenges searched for a playable round (the newest one with one wins)
HEIGHT = 640      # The iframe's height in the copied code; the widget scrolls inside it when the answer is longer
MAX_WIDTH = 480


def settings(args, languages):
    """(ui language, theme) from the widget's query string, falling back to English and the visitor's own theme."""
    lang = args.get('lang', 'en')
    theme = args.get('theme', 'auto')
    return (lang if lang in languages else 'en'), (theme if theme in THEMES else 'auto')


def _seeded(seed):
    return random.Random(int(hashlib.sha256(seed.encode('utf-8')).hexdigest(), 16))


def round_of_the_day(dailies, hidden, date):
    """(daily id, round index, round) to show on `date`, or None. dailies: [(id, rounds)] newest first; the first
    with a playable round is used, and the round within it is the same all day."""
    for daily_id, rounds in dailies:
        playable = playable_rounds(rounds, hidden)
        if playable:
            index, statements = _seeded(f'{date}:{daily_id}').choice(playable)
            return daily_id, index, statements
    return None


def playable_round(rounds, index, hidden):
    """The round at `index` if it can be shown (one fake among four, not hidden after a report), else None."""
    return next((statements for i, statements in playable_rounds(rounds, hidden) if i == index), None)


def option_order(statements, seed):
    """The round's statements in the order the widget shows them: shuffled, but the same for the same round, so the
    answer link can name an option by its position."""
    options = list(statements)
    _seeded(seed).shuffle(options)
    return options


def snippet(src, title):
    """The code a site owner copies: a responsive iframe."""
    return (f'<iframe src="{escape(src)}" title="{escape(title)}" width="100%" height="{HEIGHT}" '
            f'style="max-width:{MAX_WIDTH}px;border:0;border-radius:16px" loading="lazy"></iframe>')
