"""Players report rounds they think were wrong; the owner reads them with `flask reports`."""
import json

import pytest

import app as app_module
from app import db
from conftest import answers, play_quiz
from models import QuizSession, RoundReport, User


@pytest.fixture
def played(login, app_ctx):
    """A player who finished a 3-round quiz: (client, quiz)."""
    client = login('Player')
    play_quiz(client, answers(2, 3))
    return client, QuizSession.query.one()


def report(client, quiz, round_index=0, reason='fake_is_true', comment='', next_url='/results'):
    return client.post('/report', data={'quiz_session_id': quiz.id, 'round': round_index, 'reason': reason,
                                        'comment': comment, 'next': next_url})


def test_results_and_review_offer_a_report_per_round(played):
    client, quiz = played
    assert client.get('/results').get_data(as_text=True).count('Report a problem') == 3
    assert client.get(f'/review/{quiz.id}').get_data(as_text=True).count('Report a problem') == 3


def test_a_report_keeps_a_copy_of_the_round(played):
    client, quiz = played
    response = report(client, quiz, 1, 'real_is_false', 'The second fact is outdated.')
    assert response.headers['Location'] == '/results'
    saved = RoundReport.query.one()
    assert (saved.round_index, saved.reason, saved.comment, saved.topic) == (1, 'real_is_false', 'The second fact is outdated.', quiz.topic)
    assert json.loads(saved.statements_json) == json.loads(quiz.questions_json)[1]
    assert saved.explanation == 'Why round 1 is fake.'
    html = client.get('/results').get_data(as_text=True)
    assert "We&#39;ll check this round" in html and 'Reported. Thanks!' in html and html.count('Report a problem') == 2


def test_a_round_is_reported_once(played):
    client, quiz = played
    report(client, quiz)
    report(client, quiz, reason='other')
    assert RoundReport.query.count() == 1
    assert "already reported this round" in client.get('/results').get_data(as_text=True)


@pytest.mark.parametrize('changes', [{'reason': 'nonsense'}, {'round_index': 3}, {'round_index': -1}])
def test_bad_reports_are_ignored(played, changes):
    client, quiz = played
    report(client, quiz, **changes)
    assert RoundReport.query.count() == 0


def test_only_your_own_quizzes_can_be_reported(played, login):
    _, quiz = played
    report(login('Someone else'), quiz)
    assert RoundReport.query.count() == 0


def test_reports_are_rate_limited(played, monkeypatch):
    client, quiz = played
    monkeypatch.setitem(app_module.RATE_LIMITS, 'report', (1, app_module.timedelta(hours=1)))
    report(client, quiz, 0)
    report(client, quiz, 1)
    assert RoundReport.query.count() == 1


def test_redirects_stay_on_the_site(played):
    client, quiz = played
    assert report(client, quiz, next_url='https://evil.example').headers['Location'] == '/history'


def test_the_guest_round_has_no_report_form(app, app_ctx):
    """Guests have no quiz of their own to report from."""
    from datetime import timedelta
    from models import DailyChallenge, utcnow
    facts = {'A.': True, 'B.': True, 'C.': True, 'D.': False}
    db.session.add(DailyChallenge(date=(utcnow() - timedelta(days=1)).strftime('%Y-%m-%d'), language='English',
                                  topic='Cat', questions_json=json.dumps([facts]), explanations_json='["Why."]'))
    db.session.commit()
    guest = app.test_client()
    guest.get('/try')
    assert 'Report a problem' not in guest.post('/try', data={'option': 'D.'}).get_data(as_text=True)


def test_owner_reads_reports_on_the_command_line(played, app):
    client, quiz = played
    report(client, quiz, 0, 'fake_is_true', 'Pretty sure this is right.')
    output = app.test_cli_runner().invoke(args=['reports']).output
    assert 'Player' in output and '[fake_is_true]' in output and 'Fake: R0 FAKE' in output
    assert 'Pretty sure this is right.' in output


def test_deleting_a_player_deletes_their_reports(played):
    client, quiz = played
    report(client, quiz)
    app_module.delete_player_data(User.query.filter_by(username='Player').one())
    assert RoundReport.query.count() == 0
