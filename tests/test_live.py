"""Live head-to-head through the app: invite link, joining, playing on a shared clock, and the result counting
like any finished game. The rules themselves are tested in test_live_rules.py."""
from datetime import datetime, timedelta

import pytest

import app as app_module
import live
from app import db
from models import LiveMatch, QuizSession, Score, User

FORM = {'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'}  # 3 rounds


@pytest.fixture
def clock(monkeypatch):
    """The app's clock, moved by hand: clock.now, clock.tick(seconds)."""
    class Clock:
        now = datetime(2026, 11, 12, 12, 0, 0)

        def tick(self, seconds):
            self.now += timedelta(seconds=seconds)
    c = Clock()
    monkeypatch.setattr(app_module, 'utcnow', lambda: c.now)
    return c


@pytest.fixture
def match(login, clock, app_ctx):
    """A host who created a match and a friend who joined it: (host client, guest client, code)."""
    host = login('Host')
    code = host.post('/live/new', data=FORM).headers['Location'].rsplit('/', 1)[1]
    guest = login('Friend')
    guest.post(f'/live/{code}/join')
    return host, guest, code


def state(client, code):
    return client.get(f'/live/{code}/state').get_json()


def answer(client, code, round_number, option):
    return client.post(f'/live/{code}/answer', json={'round': round_number, 'option': option}).get_json()


# ---------- Making and joining a match ----------------------------------------------------------------------------

def test_setup_offers_a_live_match(login):
    html = login('Host').get('/setup').get_data(as_text=True)
    assert 'formaction="/live/new"' in html and 'Play live with a friend' in html


def test_host_gets_an_invite_link_and_waits(login, ai, clock, app_ctx):
    host = login('Host')
    location = host.post('/live/new', data=FORM).headers['Location']
    code = location.rsplit('/', 1)[1]
    assert location.startswith('/live/') and len(code) >= 12 and len(ai.quiz_calls) == 1
    html = host.get(location).get_data(as_text=True)
    assert f'/live/{code}' in html and 'Waiting for your friend to join' in html
    assert state(host, code)['phase'] == 'waiting'


def test_a_signed_out_friend_signs_in_and_comes_back(app, login, clock, app_ctx):
    code = login('Host').post('/live/new', data=FORM).headers['Location'].rsplit('/', 1)[1]
    visitor = app.test_client()
    assert visitor.get(f'/live/{code}').headers['Location'] == '/'
    with visitor.session_transaction() as sess:
        assert sess['next_url'] == f'/live/{code}'


def test_friend_sees_the_invite_and_joins(login, clock, app_ctx):
    host = login('Host')
    code = host.post('/live/new', data=FORM).headers['Location'].rsplit('/', 1)[1]
    friend = login('Friend')
    assert 'Host invites you to a live match' in friend.get(f'/live/{code}').get_data(as_text=True)
    friend.post(f'/live/{code}/join')
    assert state(friend, code)['phase'] == 'countdown'
    assert LiveMatch.query.one().guest_id == User.query.filter_by(username='Friend').one().id


def test_a_third_player_cant_join(match, login):
    _, _, code = match
    third = login('Third')
    assert third.get(f'/live/{code}').headers['Location'] == '/setup'
    third.post(f'/live/{code}/join')
    assert LiveMatch.query.one().guest_id == User.query.filter_by(username='Friend').one().id
    assert third.get(f'/live/{code}/state').status_code == 403


def test_host_cant_join_their_own_match(login, clock, app_ctx):
    host = login('Host')
    code = host.post('/live/new', data=FORM).headers['Location'].rsplit('/', 1)[1]
    host.post(f'/live/{code}/join')
    assert LiveMatch.query.one().guest_id is None


def test_an_invite_nobody_takes_expires(login, clock, app_ctx):
    host = login('Host')
    code = host.post('/live/new', data=FORM).headers['Location'].rsplit('/', 1)[1]
    clock.tick(live.WAITING_MINUTES * 60 + 1)
    assert state(host, code)['phase'] == 'expired'
    friend = login('Friend')
    friend.post(f'/live/{code}/join')
    assert LiveMatch.query.one().guest_id is None


def test_unknown_match_is_not_found(login, app_ctx):
    assert login('Host').get('/live/nope').status_code == 404


# ---------- Playing -----------------------------------------------------------------------------------------------

def test_both_players_see_the_same_round_after_the_countdown(match, clock):
    host, guest, code = match
    clock.tick(live.COUNTDOWN_SECONDS)
    h, g = state(host, code), state(guest, code)
    assert h['phase'] == g['phase'] == 'question' and h['round'] == 1 and h['rounds'] == 3
    assert h['options'] == g['options'] and sorted(h['options']) == sorted(['R0 true A', 'R0 FAKE', 'R0 true B', 'R0 true C'])
    assert 'result' not in h  # nothing about the answer before the round ends


def test_a_full_match(match, clock):
    host, guest, code = match
    for round_number in (1, 2, 3):
        clock.tick(live.COUNTDOWN_SECONDS if round_number == 1 else live.REVEAL_SECONDS)
        clock.tick(2)
        assert answer(host, code, round_number, f'R{round_number - 1} FAKE')['ok']
        assert state(guest, code)['players']['host']['answered']
        clock.tick(3)
        assert answer(guest, code, round_number, f'R{round_number - 1} true A')['ok']
        reveal = state(host, code)
        assert reveal['phase'] == 'reveal' and reveal['result']['fake'] == f'R{round_number - 1} FAKE'
        assert reveal['result']['answers']['guest']['correct'] is False
    clock.tick(live.REVEAL_SECONDS)
    final = state(guest, code)
    assert final['phase'] == 'finished' and final['winner'] == 'host'
    assert final['players']['host']['correct'] == 3 and final['players']['guest']['points'] == 0
    assert len(final['summary']) == 3


def test_answers_count_once_and_only_from_players(match, clock, login):
    host, _, code = match
    clock.tick(live.COUNTDOWN_SECONDS + 1)
    assert answer(host, code, 1, 'R0 true A')['ok']
    assert not answer(host, code, 1, 'R0 FAKE')['ok']  # no second try
    assert not answer(host, code, 2, 'R1 FAKE')['ok']  # not that round yet
    assert login('Third').post(f'/live/{code}/answer', json={'round': 1, 'option': 'R0 FAKE'}).status_code == 403
    assert host.post(f'/live/{code}/answer', data='junk').status_code == 400


def test_the_result_counts_like_a_finished_game_once(match, clock):
    host, guest, code = match
    clock.tick(live.COUNTDOWN_SECONDS + 1)
    answer(host, code, 1, 'R0 FAKE')
    clock.tick(10 * (live.ROUND_SECONDS + live.REVEAL_SECONDS))  # the rest times out
    state(host, code)
    state(guest, code)  # both pages see the end; the scores are saved only once
    scores = {db.session.get(User, s.user_id).username: s.score for s in Score.query.all()}
    assert scores == {'Host': 1, 'Friend': 0}
    host_quiz = QuizSession.query.filter_by(user_id=User.query.filter_by(username='Host').one().id).one()
    assert host_quiz.is_completed and host_quiz.total_questions == 3


def test_live_pages_have_no_help_button(match):
    host, _, code = match
    assert 'class="chatbot-fab"' not in host.get(f'/live/{code}').get_data(as_text=True)


def test_deleting_a_player_keeps_matches_hosted_by_others(match):
    friend = User.query.filter_by(username='Friend').one()
    app_module.delete_player_data(friend)
    assert LiveMatch.query.one().guest_id is None
    app_module.delete_player_data(User.query.filter_by(username='Host').one())
    assert LiveMatch.query.count() == 0


def test_opponents_points_stay_hidden_while_you_answer(match, clock):
    host, guest, code = match
    clock.tick(live.COUNTDOWN_SECONDS + 1)
    answer(host, code, 1, 'R0 FAKE')
    seen = state(guest, code)
    assert seen['players']['host']['answered'] and seen['players']['host']['points'] == 0
