"""Weekly leagues: the rules (pure, no database, no Flask).

Every week (Monday to Sunday, UTC) a player who finishes a game joins a league: a group of up to GROUP_SIZE players
in their tier. Points come from that week's finished games: correct answers, worth more on harder quizzes. When the
week ends the top quarter of each group moves up a tier and the bottom quarter moves down.

Nothing runs at midnight on Sunday (the app is serverless). A player's tier for a new week is worked out the first
time they play in it, from where they finished in the last week they played.
"""
from datetime import datetime, timedelta

TIERS = ['Bronze', 'Silver', 'Gold', 'Diamond']  # Index 0 is where everyone starts
TIER_ICONS = ['🥉', '🥈', '🥇', '💎']
GROUP_SIZE = 20
POINTS = {'Easy (Ages 7-12)': 10, 'Medium (Ages 13-17)': 15, 'Hard (Ages 18-99)': 20}  # Per correct answer
DEFAULT_POINTS = 15       # Scores from before the difficulty labels count as Medium
MOVING_SHARE = 0.25       # Of a group, at each end
MIN_SIZE_TO_DEMOTE = 8    # In a smaller group nobody moves down: there's hardly anyone to finish behind


def points_for(difficulty, correct):
    return POINTS.get(difficulty, DEFAULT_POINTS) * correct


def week_range(week):
    """(start, end) of a week key like '2026-W47': Monday 00:00 UTC to the next Monday 00:00 (end not included)."""
    start = datetime.strptime(f'{week}-1', '%G-W%V-%u')
    return start, start + timedelta(days=7)


def zones(size, tier):
    """(how many of a group of `size` move up, how many move down) from `tier`."""
    up = 0 if tier >= len(TIERS) - 1 or size < 2 else max(1, round(size * MOVING_SHARE))
    down = 0 if tier <= 0 or size < MIN_SIZE_TO_DEMOTE else int(size * MOVING_SHARE)
    return up, down


def standings(players):
    """[(rank, key, points)] best first. players: [(key, points)] in the order they joined the group, which also
    breaks ties: of two players with the same points, the one who joined first ranks higher."""
    ordered = sorted(enumerate(players), key=lambda p: (-p[1][1], p[0]))
    return [(rank, key, points) for rank, (_, (key, points)) in enumerate(ordered, start=1)]


def outcome(rank, points, size, tier):
    """'up', 'down' or 'stay' for the player ranked `rank` of `size` with `points`. Nobody moves up without points."""
    up, down = zones(size, tier)
    if rank <= up and points > 0:
        return 'up'
    if rank > size - down:
        return 'down'
    return 'stay'


def next_tier(tier, result):
    step = {'up': 1, 'down': -1}.get(result, 0)
    return min(len(TIERS) - 1, max(0, tier + step))


def next_group(sizes):
    """Which group of a tier a new player joins. sizes: [players in group 0, in group 1, ...]. The last group
    until it's full, then a new one, so groups fill up in the order players start playing."""
    if not sizes:
        return 0
    return len(sizes) - 1 if sizes[-1] < GROUP_SIZE else len(sizes)
