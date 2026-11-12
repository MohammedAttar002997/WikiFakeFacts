"""The owner's admin page: only for ADMIN_USERS, invisible to everyone else, with numbers that add up."""
import json
from datetime import timedelta

import pytest

from admin_stats import dashboard, games_per_day
from app import db
from conftest import answers, play_quiz
from models import DailyChallenge, ErrorLog, LiveMatch, RoundReport, Score, User, utcnow


@pytest.fixture
def admins(app, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_USERS', 'Boss, second admin')


# ---------- Who can see it ----------------------------------------------------------------------------------------

def test_admins_see_the_page_and_a_menu_link(admins, login):
    client = login('Boss')
    assert client.get('/admin').status_code == 200
    assert 'href="/admin"' in client.get('/setup').get_data(as_text=True)


def test_names_match_case_insensitively(admins, login):
    assert login('SECOND ADMIN').get('/admin').status_code == 200


@pytest.mark.parametrize('who', ['signed out', 'Player'])
def test_everyone_else_gets_a_plain_404(admins, app, login, who):
    client = app.test_client() if who == 'signed out' else login(who)
    assert client.get('/admin').status_code == 404
    assert 'href="/admin"' not in client.get('/').get_data(as_text=True)


def test_without_admin_users_the_page_doesnt_exist(app, login, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_USERS', '')
    assert login('Boss').get('/admin').status_code == 404


# ---------- The numbers -------------------------------------------------------------------------------------------

def test_games_players_and_ai_quizzes_add_up(admins, login, app_ctx):
    for name in ('Ann', 'Ben'):
        play_quiz(login(name), answers(2, 3))
    db.session.add(DailyChallenge(date='2026-01-01', language='English', topic='Cat', questions_json='[]',
                                  explanations_json='[]', created_at=utcnow()))
    db.session.commit()
    stats = dashboard(utcnow())
    assert stats['totals']['games'] == 2 and stats['games']['Today'] == 2 and stats['players']['7 days'] == 2
    assert stats['ai']['30 days'] == {'games': 2, 'daily': 1, 'live': 0, 'uploads': 0, 'total': 3}
    assert sum(n for _, n in stats['per_day']) == 2


def test_old_activity_falls_out_of_the_windows(admins, app_ctx):
    user = User(username='Old', created_at=utcnow() - timedelta(days=60))
    db.session.add(user)
    db.session.commit()
    db.session.add(Score(user_id=user.id, topic='Cat', difficulty='Easy (Ages 7-12)', score=1, total_questions=3,
                         percentage=33, timestamp=utcnow() - timedelta(days=40)))
    db.session.commit()
    stats = dashboard(utcnow())
    assert stats['totals']['games'] == 1 and stats['games']['30 days'] == 0 and stats['signups']['30 days'] == 0


def test_chart_has_a_bar_for_every_day_even_empty_ones(app_ctx):
    days = games_per_day(utcnow())
    assert len(days) == 30 and all(n == 0 for _, n in days)
    assert days[-1][0] == utcnow().strftime('%Y-%m-%d')


def test_topics_and_categories(admins, login, app_ctx):
    play_quiz(login('Ann'), answers(1, 3))
    stats = dashboard(utcnow())
    topic, n = stats['top_topics'][0]
    assert n == 1 and stats['categories'][0][1] == 1


def test_reports_and_errors_are_listed(admins, login, app_ctx):
    client = login('Boss')
    play_quiz(client, answers(1, 3))
    quiz_id = Score.query.one().quiz_session_id
    client.post('/report', data={'quiz_session_id': quiz_id, 'round': 0, 'reason': 'fake_is_true',
                                 'comment': 'Check this one', 'next': '/results'})
    db.session.add(ErrorLog(method='GET', path='/broken', error='ValueError: boom', traceback=''))
    db.session.commit()
    html = client.get('/admin').get_data(as_text=True)
    assert 'Check this one' in html and 'Fake:</strong> R0 FAKE' in html
    assert 'ValueError: boom' in html and '/broken' in html
    assert RoundReport.query.count() == 1


def test_live_matches_by_state(admins, login, app_ctx):
    host = User.query.filter_by(username='Boss').first() or User(username='Boss')
    db.session.add(host)
    db.session.commit()
    db.session.add(LiveMatch(code='abc', host_id=host.id, topic='Cat', difficulty='x', language='English',
                             questions_json=json.dumps([]), explanations_json='[]', state='finished',
                             current_round=0, answers_json='{}', version=0, scores_saved=True, created_at=utcnow()))
    db.session.commit()
    assert dashboard(utcnow())['live'] == {'finished': 1}
