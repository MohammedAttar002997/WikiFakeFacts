"""Timed mode: a countdown per round, enforced by the server."""
from datetime import timedelta

import app as app_module
from app import db
from models import QuizSession, Score, utcnow


def start_timed(client, timed=True):
    data = {'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'}
    if timed:
        data['timed'] = '1'
    client.post('/start_quiz', data=data)
    return QuizSession.query.order_by(QuizSession.id.desc()).first()


def test_timed_quiz_shows_countdown(login, app_ctx):
    client = login('Player')
    quiz = start_timed(client)
    assert quiz.time_limit == app_module.TIMED_ROUND_SECONDS
    html = client.get('/quiz').get_data(as_text=True)
    assert 'id="quizTimer"' in html and 'name="timeout"' in html


def test_untimed_quiz_has_no_countdown(login, app_ctx):
    client = login('Player')
    quiz = start_timed(client, timed=False)
    assert quiz.time_limit is None
    assert 'id="quizTimer"' not in client.get('/quiz').get_data(as_text=True)


def test_reloading_does_not_restart_the_clock(login, app_ctx):
    client = login('Player')
    quiz = start_timed(client)
    client.get('/quiz')
    started = db.session.get(QuizSession, quiz.id).round_started_at
    client.get('/quiz')
    db.session.expire_all()
    assert db.session.get(QuizSession, quiz.id).round_started_at == started


def test_answer_in_time_counts(login, app_ctx):
    client = login('Player')
    start_timed(client)
    client.get('/quiz')
    client.post('/quiz', data={'option': 'R0 FAKE'})
    assert QuizSession.query.one().score == 1


def test_browser_timeout_counts_as_missed(login, app_ctx):
    client = login('Player')
    start_timed(client)
    client.get('/quiz')
    client.post('/quiz', data={'timeout': '1'})
    quiz = QuizSession.query.one()
    assert (quiz.score, quiz.current_question_index) == (0, 1)


def test_late_answer_counts_as_missed_even_if_correct(login, app_ctx):
    client = login('Player')
    quiz = start_timed(client)
    client.get('/quiz')
    quiz = db.session.get(QuizSession, quiz.id)
    quiz.round_started_at = utcnow() - timedelta(seconds=app_module.TIMED_ROUND_SECONDS + 10)
    db.session.commit()
    client.post('/quiz', data={'option': 'R0 FAKE'})  # right answer, but far too late
    db.session.expire_all()
    assert db.session.get(QuizSession, quiz.id).score == 0


def test_each_round_gets_a_fresh_clock(login, app_ctx):
    client = login('Player')
    start_timed(client)
    client.get('/quiz')
    client.post('/quiz', data={'option': 'R0 FAKE'})
    assert QuizSession.query.one().round_started_at is None
    client.get('/quiz')
    assert QuizSession.query.one().round_started_at is not None


def test_review_marks_timed_out_rounds(login, app_ctx):
    client = login('Player')
    start_timed(client)
    for pick in ('R0 FAKE', None, 'R2 true A'):
        client.get('/quiz')
        client.post('/quiz', data={'timeout': '1'} if pick is None else {'option': pick})
    html = client.get('/results').get_data(as_text=True)
    assert html.count('Time ran out') == 1
    assert Score.query.one().score == 1


def test_setup_offers_timed_mode(login):
    html = login('Player').get('/setup').get_data(as_text=True)
    assert 'name="timed"' in html
