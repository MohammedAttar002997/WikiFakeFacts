"""Friends: following other players, the friends leaderboard and feed (pure rules, no database, no Flask).

A player follows others from their profile (one-way, like most games: no requests to accept). The Friends tab of
the Players page then ranks the player and everyone they follow by this week's league points, and lists the games
those players finished lately.
"""
MAX_FOLLOWING = 200  # Players one player can follow
FEED_SIZE = 20       # Newest games of followed players shown
SUGGESTIONS = 6      # Players suggested to someone who follows nobody yet


def can_follow(me_id, them_id, already, following_count):
    """Why following isn't possible ('self', 'already', 'full'), or None when it is."""
    if me_id == them_id:
        return 'self'
    if already:
        return 'already'
    if following_count >= MAX_FOLLOWING:
        return 'full'
    return None


def leaderboard(rows, me_id):
    """[{rank, user_id, points, games, is_me}] best first: this week's points, then games, then who's the player
    (so the list reads the same way every time). rows: [(user_id, points, games)] for the player and those they follow."""
    ordered = sorted(rows, key=lambda r: (-r[1], -r[2], r[0] != me_id, r[0]))
    return [{'rank': i, 'user_id': uid, 'points': pts, 'games': games, 'is_me': uid == me_id}
            for i, (uid, pts, games) in enumerate(ordered, start=1)]


def suggestions(active, following, me_id, size=SUGGESTIONS):
    """Players to suggest: the most active this week that the player doesn't follow yet (and isn't). active: user ids,
    most active first."""
    return [uid for uid in active if uid != me_id and uid not in following][:size]
