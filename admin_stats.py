"""The numbers on the admin page (/admin): players, games, AI use, popular topics, reports and errors.

Only reads the database. Quiz sessions have no timestamp of their own, so "AI quizzes" over a period counts what
has one: finished games of freshly generated quizzes (by their score's time), quizzes made ahead of time by the
refill (when made, not when played), daily challenges, live matches and custom uploads. Replays of an existing quiz (challenges, daily attempts) made no AI call and aren't counted.
"""
import json
from collections import Counter
from datetime import timedelta

from sqlalchemy import func

from badges import TOPIC_CATEGORY
from models import (ChatbotSession, CustomContent, DailyChallenge, ErrorLog, LiveMatch, QuizSession, ReadyQuiz,
                    RoundReport, Score, User, db)

CHART_DAYS = 30


def count(query):
    return query.order_by(None).count()


def since(column, now, days):
    return column >= now - timedelta(days=days)


def generated_games(now, days):
    """Finished games of quizzes the AI made for that game (not replays of someone else's quiz, nor ready-made
    quizzes, which are counted when they're made)."""
    made_ahead = db.session.query(ReadyQuiz.quiz_session_id).filter(ReadyQuiz.quiz_session_id.isnot(None))
    return count(Score.query.join(QuizSession, Score.quiz_session_id == QuizSession.id)
                 .filter(QuizSession.challenge_of_id.is_(None), QuizSession.daily_challenge_id.is_(None),
                         QuizSession.id.not_in(made_ahead), since(Score.timestamp, now, days)))


def games_per_day(now):
    """[(date 'YYYY-MM-DD', games)] for the last CHART_DAYS days, oldest first, including days with none."""
    rows = (db.session.query(func.date(Score.timestamp), func.count(Score.id))
            .filter(since(Score.timestamp, now, CHART_DAYS)).group_by(func.date(Score.timestamp)).all())
    by_day = {str(day)[:10]: n for day, n in rows}
    days = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(CHART_DAYS - 1, -1, -1)]
    return [(day, by_day.get(day, 0)) for day in days]


def periods(now, make):
    """{label: make(days)} for today, 7 and 30 days."""
    return {'Today': make(None), '7 days': make(7), '30 days': make(30)}


def dashboard(now):
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def window(column, days):
        return column >= start_of_day if days is None else since(column, now, days)

    games = periods(now, lambda d: count(Score.query.filter(window(Score.timestamp, d))))
    players = periods(now, lambda d: db.session.query(func.count(func.distinct(Score.user_id)))
                      .filter(window(Score.timestamp, d)).scalar() or 0)
    signups = periods(now, lambda d: count(User.query.filter(window(User.created_at, d))))

    def ai_quizzes(days):
        span = (now - start_of_day).total_seconds() / 86400 if days is None else days
        return {'games': generated_games(now, span),
                'ready': count(ReadyQuiz.query.filter(window(ReadyQuiz.created_at, days))),
                'daily': count(DailyChallenge.query.filter(window(DailyChallenge.created_at, days))),
                'live': count(LiveMatch.query.filter(window(LiveMatch.created_at, days))),
                'uploads': count(CustomContent.query.filter(window(CustomContent.created_at, days)))}
    ai = periods(now, ai_quizzes)
    for numbers in ai.values():
        numbers['total'] = sum(numbers.values())

    chats = periods(now, lambda d: count(ChatbotSession.query.filter(window(ChatbotSession.updated_at, d))))

    month = Score.query.filter(since(Score.timestamp, now, 30)).with_entities(Score.topic).all()
    topics = Counter(t for (t,) in month)
    categories = Counter(TOPIC_CATEGORY.get(t, 'Custom quizzes') for (t,) in month)

    reports = []
    for r in RoundReport.query.order_by(RoundReport.id.desc()).limit(20):
        facts = json.loads(r.statements_json)
        user = db.session.get(User, r.user_id)
        reports.append({'report': r, 'user': user.username if user else '?',
                        'fake': next((s for s, real in facts.items() if real is False), '?'),
                        'true': [s for s, real in facts.items() if real]})

    live = dict(db.session.query(LiveMatch.state, func.count(LiveMatch.id))
                .filter(since(LiveMatch.created_at, now, 30)).group_by(LiveMatch.state).all())

    return {
        'totals': {'players': count(User.query), 'games': count(Score.query),
                   'reports': count(RoundReport.query), 'errors_7d': count(ErrorLog.query.filter(
                       since(ErrorLog.created_at, now, 7)))},
        'games': games, 'players': players, 'signups': signups, 'ai': ai, 'chats': chats,
        'per_day': games_per_day(now),
        'top_topics': topics.most_common(10), 'categories': categories.most_common(),
        'live': live, 'reports': reports,
        'ready_in_stock': count(ReadyQuiz.query.filter(ReadyQuiz.used_at.is_(None))),
        'errors': ErrorLog.query.order_by(ErrorLog.id.desc()).limit(10).all(),
    }
