"""The detective's desk at the top of /setup: today's case (the daily challenge) and a few notes about the player.
Data only; templates/setup.html draws it. Called from app.render_setup.
"""
import app as core
from app import db
from models import DailyChallenge, QuizSession, Score
from views.help_chat import player_summary


def case_number(date):
    """Today's case number as shown on the folder: the date's month and day, "2026-11-25" -> "1125"."""
    return date[5:7] + date[8:10]


def daily_counts(date):
    """(players who finished the day's challenge, how many of them found every fake), in every language."""
    rows = (db.session.query(Score.score, Score.total_questions)
            .join(QuizSession, Score.quiz_session_id == QuizSession.id)
            .join(DailyChallenge, QuizSession.daily_challenge_id == DailyChallenge.id)
            .filter(DailyChallenge.date == date))
    played = solved = 0
    for score, total in rows:
        played += 1
        solved += score == total
    return played, solved


def desk(user):
    """Everything the desk shows for a signed-in player."""
    summary = player_summary(user)
    today = core.today_utc()
    played, solved = daily_counts(today)
    attempt = core.daily_attempt(user, today)
    my_score = Score.query.filter_by(quiz_session_id=attempt.id).first() if attempt else None
    return {**summary, 'case_no': case_number(today), 'played': played, 'solved': solved, 'attempt': attempt,
            'my_score': my_score, 'event_theme': core.event_theme()}
