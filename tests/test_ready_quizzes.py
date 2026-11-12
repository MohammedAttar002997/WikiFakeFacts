"""Ready-made quizzes: /cron/refill makes them ahead of time, /start_quiz hands each one to a single player."""
import json
from datetime import timedelta

import pytest

import app as app_module
import ready_pool
from app import db
from conftest import answers, play_quiz
from default_text import CATEGORIES, DIFFICULTY_OPTIONS
from models import ErrorLog, QuizDemand, QuizSession, ReadyQuiz, Score, User, utcnow

SECRET = 'test-cron-secret'
KIND = ready_pool.Kind(CATEGORIES['1'], DIFFICULTY_OPTIONS['2'], 3, 'English')  # What play_quiz asks for by default


@pytest.fixture
def cron(app, monkeypatch):
    monkeypatch.setitem(app.config, 'CRON_SECRET', SECRET)
    client = app.test_client()
    return lambda: client.get('/cron/refill', headers={'Authorization': f'Bearer {SECRET}'})


def stock(kind=KIND):
    return ReadyQuiz.query.filter_by(**kind._asdict(), used_at=None).count()


def want(kind, when=None):
    db.session.add(QuizDemand(**kind._asdict(), last_wanted=when or utcnow()))
    db.session.commit()


@pytest.fixture
def stocked(cron, ai, app_ctx):
    """KIND was wanted and the refill ran; the AI calls it made are forgotten."""
    want(KIND)
    cron()
    ai.quiz_calls.clear()


# ---------- Who may call the refill -------------------------------------------------------------------------------

@pytest.mark.parametrize('header', [None, 'Bearer wrong', SECRET])
def test_refill_needs_the_secret(app, monkeypatch, ai, header):
    monkeypatch.setitem(app.config, 'CRON_SECRET', SECRET)
    headers = {'Authorization': header} if header else {}
    assert app.test_client().get('/cron/refill', headers=headers).status_code == 401
    assert ai.quiz_calls == []


def test_without_a_secret_set_nobody_can_call_it(app, monkeypatch):
    monkeypatch.setitem(app.config, 'CRON_SECRET', '')
    assert app.test_client().get('/cron/refill', headers={'Authorization': 'Bearer '}).status_code == 401


# ---------- Making them ------------------------------------------------------------------------------------------

def test_starting_a_quiz_records_what_was_wanted(login, app_ctx):
    play_quiz(login('Ann'), answers(1, 3))
    demand = QuizDemand.query.one()
    assert ready_pool.Kind(demand.category, demand.difficulty, demand.rounds, demand.language) == KIND


def test_refill_makes_the_wanted_kinds(cron, ai, app_ctx):
    want(KIND)
    response = cron()
    assert response.status_code == 200 and response.get_json()['made'] == ready_pool.TARGET_STOCK
    assert stock() == ready_pool.TARGET_STOCK
    call = ai.quiz_calls[0]
    assert (call['n'], call['difficulty'], call['language']) == (3, KIND.difficulty, 'English')
    assert call['topic'] in app_module.TOPICS[KIND.category]
    assert cron().get_json()['made'] == 0  # Already full


def test_kinds_nobody_wanted_lately_are_not_made(cron, ai, app_ctx):
    want(KIND, utcnow() - timedelta(days=ready_pool.WANTED_DAYS + 1))
    assert cron().get_json()['made'] == 0 and ai.quiz_calls == []


def test_the_daily_cap_holds(cron, monkeypatch, app_ctx):
    monkeypatch.setattr(ready_pool, 'DAILY_CAP', 3)
    for rounds in (3, 5, 10):
        want(KIND._replace(rounds=rounds))
    assert cron().get_json()['made'] == 3
    assert cron().get_json()['made'] == 0


def test_a_run_stops_starting_quizzes_after_its_time(app, app_ctx):
    want(KIND)
    ticks = iter([0, 0, ready_pool.RUN_SECONDS + 1])
    with app.test_request_context('/cron/refill'):
        assert app_module.refill_ready_quizzes(clock=lambda: next(ticks))['made'] == 1


def test_a_failing_ai_stops_the_run_and_is_logged(cron, monkeypatch, app_ctx):
    want(KIND._replace(rounds=5))
    want(KIND)
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('OpenAI is down')
    monkeypatch.setattr(app_module, 'get_ai_response', broken)
    result = cron().get_json()
    assert result['made'] == 0 and 'OpenAI is down' in result['error'] and len(calls) == 1
    assert ErrorLog.query.one().path == '/cron/refill'


# ---------- Handing them out --------------------------------------------------------------------------------------

def test_a_ready_quiz_starts_without_calling_the_ai(stocked, ai, login):
    play_quiz(login('Ann'), answers(3, 3))
    assert ai.quiz_calls == [] and stock() == ready_pool.TARGET_STOCK - 1
    quiz = QuizSession.query.one()
    ready = ReadyQuiz.query.filter(ReadyQuiz.used_at.isnot(None)).one()
    assert ready.quiz_session_id == quiz.id and quiz.topic == ready.topic
    assert json.loads(quiz.questions_json) == json.loads(ready.questions_json)
    assert quiz.source_url == ready.source_url
    assert Score.query.one().score == 3


def test_each_ready_quiz_goes_to_one_player_only(stocked, ai, login):
    for name in ('Ann', 'Ben', 'Cat'):
        play_quiz(login(name), answers(1, 3))
    given = [r.quiz_session_id for r in ReadyQuiz.query.filter(ReadyQuiz.used_at.isnot(None))]
    assert len(given) == len(set(given)) == ready_pool.TARGET_STOCK
    assert len(ai.quiz_calls) == 1  # The third player got a freshly made one, as before


def test_another_kind_is_not_handed_out(stocked, ai, login):
    play_quiz(login('Ann'), answers(1, 5), length='2')
    assert len(ai.quiz_calls) == 1 and stock() == ready_pool.TARGET_STOCK


def test_surprise_me_takes_a_ready_quiz_of_any_category(stocked, ai, login):
    play_quiz(login('Ann'), answers(1, 3), category='surprise')
    assert ai.quiz_calls == [] and stock() == ready_pool.TARGET_STOCK - 1


def test_timed_mode_works_with_a_ready_quiz(stocked, ai, login):
    login('Ann').post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en',
                                           'timed': '1'})
    assert ai.quiz_calls == [] and QuizSession.query.one().time_limit == app_module.TIMED_ROUND_SECONDS


def test_a_taken_quiz_is_not_taken_twice(app_ctx):
    db.session.add(ReadyQuiz(**KIND._asdict(), topic='Cat', questions_json='[]', explanations_json='[]'))
    db.session.commit()
    assert app_module.take_ready_quiz(KIND) is not None
    assert app_module.take_ready_quiz(KIND) is None


def test_the_admin_page_counts_each_quiz_once(stocked, login):
    from admin_stats import dashboard
    play_quiz(login('Ann'), answers(1, 3))
    stats = dashboard(utcnow())
    assert (stats['ai']['Today']['ready'], stats['ai']['Today']['games']) == (ready_pool.TARGET_STOCK, 0)
    assert stats['ready_in_stock'] == ready_pool.TARGET_STOCK - 1


def test_deleting_a_player_keeps_the_ready_quiz_count(stocked, login):
    play_quiz(login('Ann'), answers(1, 3))
    app_module.delete_player_data(User.query.filter_by(username='Ann').one())
    assert ReadyQuiz.query.count() == ready_pool.TARGET_STOCK
    assert ReadyQuiz.query.filter(ReadyQuiz.quiz_session_id.isnot(None)).count() == 0
