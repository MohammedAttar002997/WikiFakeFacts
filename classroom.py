"""Classroom mode: the rules (pure, no database, no Flask).

A teacher (a signed-in player) opens a room from the setup page. Students join at /join with the room's code and a
nickname; no account, and nothing about them is kept once the room expires. The teacher starts the game and moves
from round to round; each round's question is open for ROUND_SECONDS or until everyone has answered, then the board
shows how the class answered until the teacher goes on. Right answers score, faster ones score more.

Nothing runs in the background (the app is serverless): every request works out the phase from the time.
"""
import re
from datetime import timedelta

ROUND_SECONDS = 20
MAX_PLAYERS = 60
NICKNAME_LENGTH = 20
ROOM_HOURS = 6        # Rooms (and their nicknames) are deleted after this long
CODE_LENGTH = 6
CODE_CHARS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # No I, L, O, 0 or 1: easy to read off a board and type
BASE_POINTS = 500
SPEED_BONUS = 500     # At most, for answering at once
LEADERBOARD_SIZE = 5  # Shown on the board between rounds


def make_code(rng):
    return ''.join(rng.choice(CODE_CHARS) for _ in range(CODE_LENGTH))


def normalize_code(text):
    """What a student typed, as a room code: spaces and dashes gone, upper case."""
    return re.sub(r'[\s-]', '', text or '').upper()[:CODE_LENGTH + 4]


def clean_nickname(text):
    """The nickname as shown, or None if it's empty or has no letter or digit."""
    name = re.sub(r'\s+', ' ', text or '').strip()[:NICKNAME_LENGTH]
    return name if re.search(r'\w', name) else None


def points_for(correct, ms, seconds=ROUND_SECONDS):
    if not correct:
        return 0
    return BASE_POINTS + round(SPEED_BONUS * max(0.0, 1 - ms / (seconds * 1000)))


def phase(state, started_at, now, answered, players, seconds=ROUND_SECONDS):
    """(phase, seconds left in it) for a room: 'lobby', 'question' (open until the time is up or everyone answered),
    'reveal' (until the teacher goes on) or 'finished'."""
    if state != 'playing':
        return state, 0
    left = (started_at + timedelta(seconds=seconds) - now).total_seconds()
    if left > 0 and (players == 0 or answered < players):
        return 'question', left
    return 'reveal', 0


def ranking(players):
    """[(rank, nickname, score)] best first; equal scores share a rank, earlier joiners listed first among them.
    players: [(nickname, score)] in the order they joined."""
    ordered = sorted(enumerate(players), key=lambda p: (-p[1][1], p[0]))
    ranked, last_score, rank = [], None, 0
    for position, (_, (nickname, score)) in enumerate(ordered, start=1):
        if score != last_score:
            rank, last_score = position, score
        ranked.append((rank, nickname, score))
    return ranked


def distribution(statements, picks):
    """{statement: how many picked it}, for every statement (zero included)."""
    counts = {s: 0 for s in statements}
    for pick in picks:
        if pick in counts:
            counts[pick] += 1
    return counts


def results_table(rounds, players):
    """The rows of the results download: a header, then one row per student, best first.

    players: [(nickname, score, {round index as str: {'correct': bool, 'points': int}})] in the order they joined.
    Each round shows the points scored, 0 for a wrong answer, or '' when the student didn't answer.
    """
    header = ['Rank', 'Nickname', 'Score', 'Correct'] + [f'Round {n}' for n in range(1, rounds + 1)]
    answers = {nickname: given for nickname, _, given in players}
    rows = [header]
    for rank, nickname, score in ranking([(n, s) for n, s, _ in players]):
        given = answers[nickname]
        cells = [given[str(i)]['points'] if str(i) in given else '' for i in range(rounds)]
        rows.append([rank, nickname, score, sum(1 for a in given.values() if a.get('correct'))] + cells)
    return rows


def next_topic(settings, topics, rng):
    """A fresh topic for "play again": from the same category, or any category for "Surprise me". A class that
    played a topic the teacher typed gets a new quiz on that same topic (there's no category to draw from)."""
    if settings.get('topic'):
        return None, settings['topic']
    category = rng.choice(sorted(topics)) if settings.get('surprise') else settings['category']
    return category, rng.choice(topics[category])


def expired(created_at, now):
    return now - created_at > timedelta(hours=ROOM_HOURS)
