"""Live head-to-head: the rules of a match, as plain functions over its state, so they can be tested without a
server and every request can work out where the match stands from the time alone.

A match is a dict:
    {'state': 'waiting' | 'playing' | 'finished' | 'expired',
     'rounds': int, 'current_round': int, 'round_started_at': datetime | None,
     'questions': [{statement: is_real}, ...],
     'answers': {'host': [entry | None, ...], 'guest': [...]}}   # one entry per round answered
with entry = {'pick': statement, 'ms': milliseconds after the round started, 'correct': bool, 'points': int}.

Nothing runs in the background (the app is serverless): whoever asks next moves the match on with advance().
"""
from datetime import timedelta

ROUND_SECONDS = 20  # To answer a round
REVEAL_SECONDS = 5  # Showing the answer before the next round
COUNTDOWN_SECONDS = 3  # Before the first round, once the friend has joined
WAITING_MINUTES = 30  # An invite nobody accepts expires
BASE_POINTS = 100  # For spotting the fake
SPEED_BONUS = 50  # At most, for answering at once; nothing for answering at the last moment
ROLES = ('host', 'guest')


def points_for(correct, ms):
    if not correct:
        return 0
    return BASE_POINTS + round(SPEED_BONUS * max(0.0, 1 - ms / (ROUND_SECONDS * 1000)))


def answer_of(match, role, index):
    answers = match['answers'][role]
    return answers[index] if index < len(answers) else None


def round_ends_at(match):
    """When the current round's question closes: once both have answered, or when time is up."""
    start = match['round_started_at']
    entries = [answer_of(match, role, match['current_round']) for role in ROLES]
    if all(entries):
        return start + timedelta(milliseconds=max(e['ms'] for e in entries))
    return start + timedelta(seconds=ROUND_SECONDS)


def advance(match, now, created_at=None):
    """Move the match on to where it is at `now`: past finished rounds (each followed by its reveal), to the end,
    or to 'expired' for an invite nobody took. Returns True if anything changed."""
    changed = False
    if match['state'] == 'waiting' and created_at and now - created_at > timedelta(minutes=WAITING_MINUTES):
        match['state'] = 'expired'
        return True
    while match['state'] == 'playing':
        if now < match['round_started_at']:
            break
        # Rounds nobody answered still end on time, and if both players are now waiting on an unanswered
        # round its end is in the future, so the loop stops there
        next_start = round_ends_at(match) + timedelta(seconds=REVEAL_SECONDS)
        if now < next_start:
            break
        for role in ROLES:  # a missed round is recorded as such, so both lists stay one entry per round
            answers = match['answers'][role]
            while len(answers) <= match['current_round']:
                answers.append(None)
        if match['current_round'] + 1 >= match['rounds']:
            match['state'] = 'finished'
        else:
            match['current_round'] += 1
            match['round_started_at'] = next_start
        changed = True
    return changed


def phase(match, now):
    """('waiting' | 'expired' | 'countdown' | 'question' | 'reveal' | 'finished', seconds until it ends or None).
    Call advance() first."""
    if match['state'] != 'playing':
        return match['state'], None
    start = match['round_started_at']
    if now < start:
        return 'countdown', (start - now).total_seconds()
    ends = round_ends_at(match)
    if now < ends:
        return 'question', (ends - now).total_seconds()
    return 'reveal', (ends + timedelta(seconds=REVEAL_SECONDS) - now).total_seconds()


def record_answer(match, role, round_index, pick, now):
    """Record `role`'s pick for the round, if that round is open and they haven't answered it yet. Returns the
    entry, or None if the answer doesn't count (too late, wrong round, already answered, not an option)."""
    if match['state'] != 'playing' or round_index != match['current_round'] or phase(match, now)[0] != 'question':
        return None
    facts = match['questions'][round_index]
    if pick not in facts or answer_of(match, role, round_index):
        return None
    ms = int((now - match['round_started_at']).total_seconds() * 1000)
    correct = facts[pick] is False
    entry = {'pick': pick, 'ms': ms, 'correct': correct, 'points': points_for(correct, ms)}
    answers = match['answers'][role]
    while len(answers) < round_index:
        answers.append(None)
    answers.append(entry)
    return entry


def totals(match, rounds=None):
    """{role: {'points': int, 'correct': int}} over the first `rounds` rounds (all by default)."""
    def counted(role):
        return [e for e in match['answers'][role][:rounds] if e]
    return {role: {'points': sum(e['points'] for e in counted(role)),
                   'correct': sum(1 for e in counted(role) if e['correct'])} for role in ROLES}


def visible_totals(match, now):
    """The scores a player may see: while a round is open, without it, so an opponent's jump in points doesn't
    give away that their answer was right."""
    open_round = phase(match, now)[0] in ('countdown', 'question')
    return totals(match, match['current_round'] if open_round else None)


def winner(match):
    """'host', 'guest' or None for a draw: more points wins."""
    t = totals(match)
    if t['host']['points'] == t['guest']['points']:
        return None
    return 'host' if t['host']['points'] > t['guest']['points'] else 'guest'


def start(match, now):
    """The friend has joined: the first round starts after a short countdown."""
    match['state'] = 'playing'
    match['current_round'] = 0
    match['round_started_at'] = now + timedelta(seconds=COUNTDOWN_SECONDS)
