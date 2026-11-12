"""The leaderboard rating: accuracy weighted by difficulty, made cautious for players with few answers.

- Each right answer earns credit by the quiz's difficulty: Easy 0.5, Medium 0.75, Hard 1 (×1, ×1.5, ×2), so
  playing only Easy can't top the board (a perfect Easy record is worth 50%).
- Accuracy = credit earned / questions answered.
- The rating is the lower bound of the Wilson score interval (95%) for that accuracy, on a 0-100 scale. With few
  answers the bound sits well below the raw accuracy and rises toward it as the player answers more, so a lucky
  3 out of 3 doesn't beat a steady 45 out of 50, while a careful player can still overtake one who just plays a lot.
"""
import math

CREDIT = {'Easy (Ages 7-12)': 0.5, 'Medium (Ages 13-17)': 0.75, 'Hard (Ages 18-99)': 1.0}
DEFAULT_CREDIT = 0.75  # Scores from before the difficulty labels ('Medium' etc.) count as Medium
Z = 1.96  # 95% confidence


def wilson_lower(p, n, z=Z):
    """Lower bound of the Wilson score interval for a success rate `p` over `n` trials (0 when n is 0)."""
    if n <= 0:
        return 0.0
    z2 = z * z
    centre = p + z2 / (2 * n)
    spread = z * math.sqrt(max(p * (1 - p), 0) / n + z2 / (4 * n * n))
    return max(0.0, (centre - spread) / (1 + z2 / n))


def rating(groups):
    """Rating 0-100 from [(difficulty, correct, questions)], one entry per difficulty a player played."""
    questions = sum(q for _, _, q in groups)
    if not questions:
        return 0.0
    credit = sum(CREDIT.get(d, DEFAULT_CREDIT) * c for d, c, _ in groups)
    return round(wilson_lower(credit / questions, questions) * 100, 1)


def rank(players):
    """Players ({'groups': [...], ...}) with 'rating' added, best first: higher rating, then more questions."""
    for p in players:
        p['rating'] = rating(p['groups'])
        p['correct'] = sum(c for _, c, _ in p['groups'])
        p['total_questions'] = sum(q for _, _, q in p['groups'])
        p['percentage'] = p['correct'] / p['total_questions'] * 100 if p['total_questions'] else 0
    return sorted(players, key=lambda p: (-p['rating'], -p['total_questions']))
