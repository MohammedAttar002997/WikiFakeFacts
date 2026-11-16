"""The owner acts on reports from /admin: hide a wrong round for good, or mark the report as checked."""
import json
from datetime import timedelta

import pytest

import app as app_module
from app import db
from conftest import answers, play_quiz
from hidden_rounds import fingerprint
from models import DailyChallenge, HiddenRound, QuizSession, RoundReport, utcnow

R0 = {'A.': True, 'B.': True, 'C.': True, 'D.': False}
R1 = {'E.': True, 'F.': False, 'G.': True, 'H.': True}
R2 = {'I.': False, 'J.': True, 'K.': True, 'L.': True}


@pytest.fixture
def admins(app, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_USERS', 'Boss')


def report_round(client, quiz, round_index=0):
    client.post('/report', data={'quiz_session_id': quiz.id, 'round': round_index, 'reason': 'fake_is_true',
                                 'next': '/results'})


@pytest.fixture
def reported(login, app_ctx):
    """Ann and Ben each played a quiz and reported its first round (the stub makes them the same round)."""
    for name in ('Ann', 'Ben'):
        client = login(name)
        play_quiz(client, answers(1, 3))
        report_round(client, QuizSession.query.order_by(QuizSession.id.desc()).first())
    return RoundReport.query.order_by(RoundReport.id).all()


def act(client, report, action):
    return client.post(f'/admin/reports/{report.id}/{action}')


def hide(statements):
    db.session.add(HiddenRound(fingerprint=fingerprint(statements), topic='Cat', statements_json=json.dumps(statements)))
    db.session.commit()


def daily(date, rounds, language='English'):
    challenge = DailyChallenge(date=date, language=language, topic='Cat', questions_json=json.dumps(rounds),
                               explanations_json=json.dumps([f'Why {i}' for i in range(len(rounds))]),
                               quotes_json=json.dumps([f'Quote {i}' for i in range(len(rounds))]))
    db.session.add(challenge)
    db.session.commit()
    return challenge


# ---------- Acting on reports -------------------------------------------------------------------------------------

@pytest.mark.parametrize('who', ['signed out', 'Ann'])
def test_only_admins_can_act(admins, app, login, reported, who):
    client = app.test_client() if who == 'signed out' else login(who)
    assert act(client, reported[0], 'hide').status_code == 404
    assert HiddenRound.query.count() == 0 and RoundReport.query.filter(RoundReport.status.isnot(None)).count() == 0


def test_unknown_actions_are_404(admins, login, reported):
    assert act(login('Boss'), reported[0], 'delete').status_code == 404


def test_hiding_a_round_handles_every_report_of_it(admins, login, reported):
    response = act(login('Boss'), reported[0], 'hide')
    assert response.status_code == 302 and response.headers['Location'].endswith('/admin#reports')
    assert {r.status for r in RoundReport.query} == {'hidden'}
    assert HiddenRound.query.one().fingerprint == fingerprint(json.loads(reported[0].statements_json))


def test_checking_leaves_the_round_playable(admins, login, reported):
    act(login('Boss'), reported[1], 'check')
    assert {r.status for r in RoundReport.query} == {'checked'} and HiddenRound.query.count() == 0


def test_undo_reopens_and_unhides(admins, login, reported):
    boss = login('Boss')
    act(boss, reported[0], 'hide')
    act(boss, reported[0], 'reopen')
    assert {r.status for r in RoundReport.query} == {None} and HiddenRound.query.count() == 0


def test_the_page_lists_open_and_handled_reports_apart(admins, login, reported):
    boss = login('Boss')
    assert boss.get('/admin').get_data(as_text=True).count('Hide this round</button>') == 2
    act(boss, reported[0], 'hide')
    html = boss.get('/admin').get_data(as_text=True)
    assert 'Hide this round</button>' not in html and 'No open reports.' in html
    assert 'Handled reports' in html and html.count('>Undo</button>') == 2 and '1 round hidden' in html


# ---------- Hidden rounds aren't served again ---------------------------------------------------------------------

def test_the_daily_challenge_leaves_a_hidden_round_out(login, app_ctx):
    daily(app_module.today_utc(), [R0, R1, R2])
    hide(R1)
    login('Ann').post('/daily/start')
    quiz = QuizSession.query.one()
    assert json.loads(quiz.questions_json) == [R0, R2] and quiz.total_questions == 2
    assert json.loads(quiz.explanations_json) == ['Why 0', 'Why 2']
    assert json.loads(quiz.quotes_json) == ['Quote 0', 'Quote 2']


def test_the_guest_round_never_picks_a_hidden_round(app_ctx):
    yesterday = (utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    challenge = daily(yesterday, [R0, R1])
    hide(R0)
    assert {app_module.pick_guest_round('English') for _ in range(30)} == {(challenge, 1)}
    hide(R1)
    assert app_module.pick_guest_round('English') is None


def test_a_quiz_with_a_hidden_round_cant_be_replayed_as_a_challenge(login, app_ctx):
    play_quiz(login('Ann'), answers(1, 3))
    original = QuizSession.query.one()
    original.share_code = 'abc123'
    db.session.commit()
    hide(json.loads(original.questions_json)[2])
    response = login('Ben').post('/r/abc123/play')
    assert response.headers['Location'].endswith('/setup') and QuizSession.query.count() == 1


def test_reports_store_the_rounds_fingerprint(reported):
    assert [r.fingerprint for r in reported] == [fingerprint(json.loads(r.statements_json)) for r in reported]


def test_reports_without_a_fingerprint_are_matched_too(admins, login, reported):
    """Reports made by the old code while migration 0024 was being applied have none; acting on one fills them in."""
    for r in reported:
        r.fingerprint = None
    db.session.commit()
    act(login('Boss'), reported[0], 'check')
    assert {(r.status, r.fingerprint is not None) for r in RoundReport.query} == {('checked', True)}
