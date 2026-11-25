"""Themes: which look the site uses, and the few words that change with it.

A theme is a stylesheet (public/static/css/theme-<name>.css, picked by <html data-theme>) plus, optionally, its own
words for a handful of places: the case file says "Accuse this one" where a plainer look says "Submit Answer".
Templates ask for a word with {{ word('submit_answer') }}; it falls back to WORDS[DEFAULT] and is translated like any
other string (translations.dynamic_strings lists every theme's words). No database, no settings: one theme for now.
"""

THEME = 'casefile'
DEFAULT = 'plain'

WORDS = {
    'plain': {
        'submit_answer': 'Submit Answer',
        'quiz_prompt': 'Three of these are true. Which one is the fake?',
    },
    'casefile': {
        'submit_answer': 'Accuse this one',
        'quiz_prompt': "Three of these are true. Who's lying?",
    },
}


def word(key, theme=THEME):
    """The English text for `key` in `theme` (falling back to the plain words); callers translate it."""
    return WORDS.get(theme, {}).get(key) or WORDS[DEFAULT][key]


def all_words():
    return {text for words in WORDS.values() for text in words.values()}
