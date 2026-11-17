"""Player-written fakes: the rules (pure, no database, no AI).

A player who finished a quiz can take one of its rounds, keep its three true facts and write their own fake. The AI
checks it against the round's Wikipedia article (data.check_player_fake) before it can be shared. Others then play
it from its link (/f/<code>) or from the community feed (/fakes), and the author sees how many it fooled.
"""
import random
import re

MIN_LENGTH = 15    # Characters; shorter isn't a statement anyone could believe
MAX_LENGTH = 200   # Like the AI's statements, which fit a card on a phone
TRICKY_MIN_PLAYS = 3  # A fake needs this many plays before "trickiest" ranks it, so one lucky fool doesn't top it
PAGE_SIZE = 20

LINK = re.compile(r'https?://|www\.|\.(com|net|org|de|io)\b', re.I)


def clean(text):
    """The fake as it will be shown: surrounding spaces gone, inner runs of spaces and newlines as one space."""
    return re.sub(r'\s+', ' ', text or '').strip()


def problem(fake, real_facts):
    """Why `fake` can't be submitted, as a short code, or None. Checked before the AI is asked, so obvious
    mistakes cost nothing: 'too_short', 'too_long', 'same_as_real' or 'link'."""
    if len(fake) < MIN_LENGTH:
        return 'too_short'
    if len(fake) > MAX_LENGTH:
        return 'too_long'
    if fake.casefold().rstrip('.!') in {f.casefold().rstrip('.!') for f in real_facts}:
        return 'same_as_real'
    if LINK.search(fake):
        return 'link'
    return None


def options(real_facts, fake, code):
    """The four statements in the order everyone sees them: shuffled, but the same each time for one fake."""
    statements = list(real_facts) + [fake]
    random.Random(code).shuffle(statements)
    return statements


def fooled_share(plays, fooled):
    """Share of players the fake fooled, 0 to 1 (0 with no plays)."""
    return fooled / plays if plays else 0.0


def trickiest_key(plays, fooled, created_order):
    """Sort key for the "trickiest" feed: fakes with enough plays by share fooled, then the newest; the rest after."""
    enough = plays >= TRICKY_MIN_PLAYS
    return (not enough, -fooled_share(plays, fooled) if enough else 0.0, -created_order)
