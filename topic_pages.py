"""Public topic pages for search engines: the rules (pure, no database, no Flask).

Every built-in topic has a page at /topic/<slug> with one playable sample round, taken from a round someone already
played (never a freshly made one: crawlers visit every page, and each visit must not cost an AI call). Topics with
no round yet still get a page, marked noindex so search engines don't list an empty page, and are left out of the
sitemap until they have one.
"""
import hashlib
import re
import unicodedata

from hidden_rounds import fingerprint


def slugify(title):
    """'Eiffel Tower' -> 'eiffel-tower'; accents and apostrophes dropped ("Côte d'Ivoire" -> 'cote-divoire'),
    anything else that isn't a letter or digit becomes '-'."""
    title = re.sub("['’]", '', title)
    ascii_title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '-', ascii_title.lower()).strip('-')


def slug_map(topics):
    """{slug: topic} for every topic with a usable slug; when two topics share a slug the first (sorted) keeps it, so
    the same topic always gets the same address."""
    slugs = {}
    for topic in sorted(topics):
        slug = slugify(topic)
        if slug and slug not in slugs:
            slugs[slug] = topic
    return slugs


def playable_rounds(rounds, hidden):
    """The rounds (each {statement: is_real}) that can be shown: exactly one fake among four, and not hidden after a
    report. Returned as (index, round) so the caller can find a round again."""
    good = []
    for i, statements in enumerate(rounds):
        if (isinstance(statements, dict) and len(statements) == 4
                and sum(1 for real in statements.values() if real is False) == 1
                and fingerprint(statements) not in hidden):
            good.append((i, statements))
    return good


def pick(candidates, seed):
    """One of the candidate rounds, the same one for the same seed (the page and the day), so a page doesn't change
    on every visit but does over time. candidates: [(key, round)]."""
    if not candidates:
        return None
    digest = int(hashlib.sha256(seed.encode('utf-8')).hexdigest(), 16)
    return candidates[digest % len(candidates)]


def description(topic, category):
    """The page's meta description (English: it's what search engines show)."""
    where = f' from {category}' if category else ''
    return (f'Three facts about {topic} are true and one is fake. Can you spot it? '
            f'A free Wikipedia trivia game{where}: play a sample round, then a full quiz.')
