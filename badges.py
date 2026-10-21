"""Badges and daily play streaks, worked out from a player's finished quizzes."""
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from default_text import MENU
from models import db, Score, QuizSession, UserBadge

TOPIC_CATEGORY = {topic: category for category, topics in MENU.items() for topic in topics}

# Shown in this order on My Progress
BADGES = [
    {'key': 'first_quiz', 'icon': '🎯', 'name': 'First steps', 'description': 'Finish your first quiz.'},
    {'key': 'quiz_10', 'icon': '🧠', 'name': 'Quiz regular', 'description': 'Finish 10 quizzes.'},
    {'key': 'quiz_50', 'icon': '🏛️', 'name': 'Fact-checker', 'description': 'Finish 50 quizzes.'},
    {'key': 'perfect', 'icon': '💯', 'name': 'Perfect round', 'description': 'Spot every fake in a quiz of 5 or more rounds.'},
    {'key': 'hard_ace', 'icon': '⚡', 'name': 'Sharp mind', 'description': 'Score 80% or more on a Hard quiz.'},
    {'key': 'streak_3', 'icon': '🔥', 'name': 'On a roll', 'description': 'Play 3 days in a row.'},
    {'key': 'streak_7', 'icon': '📆', 'name': 'Week warrior', 'description': 'Play 7 days in a row.'},
    {'key': 'explorer', 'icon': '🧭', 'name': 'Explorer', 'description': 'Play a quiz in every category.'},
    {'key': 'daily', 'icon': '🌅', 'name': 'Daily player', 'description': 'Finish a daily challenge.'},
    {'key': 'custom', 'icon': '📄', 'name': 'Bring your own', 'description': 'Finish a quiz made from your own content.'},
    {'key': 'comeback', 'icon': '📈', 'name': 'Comeback', 'description': 'Score 80% or more on a topic where you once scored under 50%.'},
]
BADGES_BY_KEY = {b['key']: b for b in BADGES}


def compute_streaks(play_dates, today=None):
    """(current, best) runs of consecutive days with at least one finished quiz.

    The current streak is still alive if the last play was today or yesterday."""
    today = today or datetime.utcnow().date()
    days = sorted(set(play_dates))
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    current = run if days and today - days[-1] <= timedelta(days=1) else 0
    return current, best


def earned_badge_keys(scores, daily_completed, today=None):
    """Every badge the given scores qualify for. `scores` must be oldest first."""
    earned = set()
    if not scores:
        return earned
    earned.add('first_quiz')
    if len(scores) >= 10:
        earned.add('quiz_10')
    if len(scores) >= 50:
        earned.add('quiz_50')
    if any(s.total_questions >= 5 and s.score == s.total_questions for s in scores):
        earned.add('perfect')
    if any(s.difficulty.startswith('Hard') and s.percentage >= 80 for s in scores):
        earned.add('hard_ace')

    _, best = compute_streaks([s.timestamp.date() for s in scores], today)
    if best >= 3:
        earned.add('streak_3')
    if best >= 7:
        earned.add('streak_7')

    if {TOPIC_CATEGORY[s.topic] for s in scores if s.topic in TOPIC_CATEGORY} >= set(MENU):
        earned.add('explorer')
    if daily_completed:
        earned.add('daily')
    if any(s.topic not in TOPIC_CATEGORY for s in scores):
        earned.add('custom')

    struggled = set()
    for s in scores:
        if s.topic in struggled and s.percentage >= 80:
            earned.add('comeback')
            break
        if s.percentage < 50:
            struggled.add(s.topic)
    return earned


def award_badges(user):
    """Save any badges the player newly qualifies for and return their definitions."""
    scores = Score.query.filter_by(user_id=user.id).order_by(Score.timestamp, Score.id).all()
    daily_completed = QuizSession.query.filter(QuizSession.user_id == user.id, QuizSession.is_completed.is_(True),
                                               QuizSession.daily_challenge_id.isnot(None)).first() is not None
    have = {b.badge for b in UserBadge.query.filter_by(user_id=user.id)}
    new_keys = [b['key'] for b in BADGES if b['key'] in earned_badge_keys(scores, daily_completed) - have]
    if not new_keys:
        return []
    for key in new_keys:
        db.session.add(UserBadge(user_id=user.id, badge=key))
    try:
        db.session.commit()
    except IntegrityError:
        # Already awarded by a parallel request
        db.session.rollback()
        return []
    return [BADGES_BY_KEY[key] for key in new_keys]


def player_streaks(user):
    """(current, best) streaks for a player."""
    timestamps = [t for (t,) in db.session.query(Score.timestamp).filter_by(user_id=user.id)]
    return compute_streaks([t.date() for t in timestamps if t])
