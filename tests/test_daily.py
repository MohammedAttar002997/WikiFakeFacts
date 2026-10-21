"""The daily challenge: one shared quiz per day and language, one attempt per player."""
from sqlalchemy import text

import app as app_module
from app import db, daily_topic, get_daily_challenge, today_utc
from default_text import MENU
from models import DailyChallenge, QuizSession


def play_daily(client, right):
    client.post('/daily/start')
    for i in range(app_module.DAILY_ROUNDS):
        client.post('/quiz', data={'option': f'R{i} FAKE' if i < right else f'R{i} true A'})


def test_topic_is_stable_per_date_and_from_the_menu():
    assert daily_topic('2026-10-21') == daily_topic('2026-10-21')
    assert len({daily_topic(f'2026-11-{d:02d}') for d in range(1, 15)}) > 5
    assert daily_topic(today_utc()) in {t for topics in MENU.values() for t in topics}


def test_start_generates_daily_quiz_once_per_language(login, ai, app_ctx):
    play_daily(login('A'), 3)
    play_daily(login('B'), 5)
    daily_calls = [c for c in ai.quiz_calls if c['difficulty'] == app_module.DAILY_DIFFICULTY]
    assert len(daily_calls) == 1
    assert (daily_calls[0]['n'], daily_calls[0]['topic']) == (5, daily_topic(today_utc()))

    play_daily(login('C', language='de'), 3)
    challenges = DailyChallenge.query.filter_by(date=today_utc()).all()
    assert len(challenges) == 2
    assert len({c.topic for c in challenges}) == 1


def test_one_attempt_per_day_and_resume(login, app_ctx):
    client = login('A')
    client.post('/daily/start')
    client.post('/quiz', data={'option': 'R0 FAKE'})
    client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '1', 'language': 'en'})
    client.post('/daily/start')  # resumes, doesn't start over
    attempts = QuizSession.query.filter(QuizSession.daily_challenge_id.isnot(None)).all()
    assert (len(attempts), attempts[0].current_question_index) == (1, 1)
    for i in range(1, 5):
        client.post('/quiz', data={'option': f'R{i} FAKE'})
    assert client.post('/daily/start').headers['Location'] == '/daily'


def test_leaderboard_orders_by_score_then_finish_time(app, login):
    play_daily(login('Early'), 3)
    play_daily(login('Best'), 5)
    play_daily(login('Late'), 3)
    html = app.test_client().get('/daily').get_data(as_text=True)
    assert html.index('Best') < html.index('Early') < html.index('Late')


def test_yesterdays_attempt_does_not_block_today(login, app_ctx):
    client = login('A')
    play_daily(client, 5)
    for challenge in DailyChallenge.query.all():
        challenge.date = '2000-01-01'
    db.session.commit()
    assert client.post('/daily/start').headers['Location'] == '/quiz'


def test_concurrent_generation_keeps_one_shared_quiz(app_ctx, monkeypatch, ai):
    def racing(n, topic, difficulty, language, custom_content=None):
        # Another player saves today's quiz while this one is still generating
        with db.engine.begin() as conn:
            conn.execute(text("INSERT INTO daily_challenge (date, language, topic, questions_json, explanations_json) "
                              "VALUES ('2030-01-01', 'English', 'Theirs', '[]', '[]')"))
        return ai.quiz(n, topic, difficulty, language, custom_content)
    monkeypatch.setattr(app_module, 'get_ai_response', racing)
    challenge = get_daily_challenge('2030-01-01', 'English')
    assert challenge.topic == 'Theirs'
    assert DailyChallenge.query.filter_by(date='2030-01-01').count() == 1


def test_signed_out_visitor_is_asked_to_sign_in(app):
    assert 'href="/"' in app.test_client().get('/daily').get_data(as_text=True)
