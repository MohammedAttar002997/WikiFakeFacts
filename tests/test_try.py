"""One round without signing in, taken from a past daily challenge: no AI call, nothing saved for the visitor."""
import json
import re
from datetime import timedelta

import pytest

import app as app_module
from app import db
from models import DailyChallenge, QuizSession, Score, User, utcnow

ROUND = {'Cats purr.': True, 'Cats have whiskers.': True, 'Cats sleep a lot.': True, 'Cats are reptiles.': False}


def day(offset):
    return (utcnow() + timedelta(days=offset)).strftime('%Y-%m-%d')


def add_challenge(date, language='English', topic='Cat'):
    challenge = DailyChallenge(date=date, language=language, topic=topic, source='Wikipedia',
                               questions_json=json.dumps([ROUND]),
                               explanations_json=json.dumps(['Cats are mammals.']),
                               quotes_json=json.dumps(['The cat is a small domesticated carnivorous mammal.']),
                               source_url='https://en.wikipedia.org/wiki/Cat')
    db.session.add(challenge)
    db.session.commit()
    return challenge


def options(html):
    return re.findall(r'name="option" id="opt\d" value="([^"]+)"', html)


@pytest.fixture
def guest(app):
    return app.test_client()


def test_guest_gets_a_round_from_a_past_daily_challenge(guest, app_ctx, ai):
    add_challenge(day(-1))
    html = guest.get('/try').get_data(as_text=True)
    assert sorted(options(html)) == sorted(ROUND) and 'Cat' in html
    assert ai.quiz_calls == []  # no AI call
    assert User.query.count() == QuizSession.query.count() == Score.query.count() == 0


def test_todays_challenge_is_never_spoiled(guest, app_ctx):
    add_challenge(day(0))
    response = guest.get('/try')
    assert response.headers['Location'] == '/'  # nothing to try yet
    assert 'no rounds to try yet' in guest.get('/').get_data(as_text=True)


def test_round_comes_in_the_visitors_language_or_english(guest, app_ctx):
    add_challenge(day(-2), language='Deutsch (German)', topic='Katze')
    add_challenge(day(-1), language='English', topic='Cat')
    with guest.session_transaction() as sess:
        sess['language'] = 'de'
    assert 'Katze' in guest.get('/try').get_data(as_text=True)
    with guest.session_transaction() as sess:
        sess['language'] = 'fr'
        sess.pop('guest_round')
    assert '<i class="bi bi-tag-fill"></i>Cat<' in guest.get('/try').get_data(as_text=True)


def test_spotting_the_fake(guest, app_ctx):
    add_challenge(day(-1))
    guest.get('/try')
    html = guest.post('/try', data={'option': 'Cats are reptiles.'}).get_data(as_text=True)
    assert 'Spotted it' in html and 'Cats are mammals.' in html  # the explanation
    assert 'Play a full game' in html and 'Try another round' in html


def test_missing_the_fake_shows_which_one_it_was(guest, app_ctx):
    add_challenge(day(-1))
    guest.get('/try')
    html = guest.post('/try', data={'option': 'Cats purr.'}).get_data(as_text=True)
    assert 'Missed' in html
    fake = re.search(r'class="review-fact is-fake">.*?<span class="flex-grow-1">([^<]+)<', html, re.S).group(1)
    assert fake == 'Cats are reptiles.'


def test_answers_are_checked_against_the_round_in_the_session(guest, app_ctx):
    add_challenge(day(-1))
    assert guest.post('/try', data={'option': 'Cats are reptiles.'}).headers['Location'] == '/try'  # no round yet
    guest.get('/try')
    assert guest.post('/try', data={'option': 'Made-up answer'}).headers['Location'] == '/try'


def test_a_round_is_answered_once(guest, app_ctx):
    add_challenge(day(-1))
    guest.get('/try')
    guest.post('/try', data={'option': 'Cats purr.'})
    # Refreshing the result doesn't let the guest try the same round again with the answer known
    assert guest.post('/try', data={'option': 'Cats are reptiles.'}).headers['Location'] == '/try'


def test_home_offers_the_guest_round_only_to_visitors(guest, login):
    assert 'Try a round without signing up' in guest.get('/').get_data(as_text=True)
    assert 'Try a round without signing up' not in login('Player').get('/').get_data(as_text=True)


def test_help_button_stays_off_the_guest_round(guest, app_ctx):
    add_challenge(day(-1))
    assert 'class="chatbot-fab"' not in guest.get('/try').get_data(as_text=True)


def test_guest_round_picks_among_recent_challenges(app_ctx, monkeypatch):
    for offset in range(1, app_module.GUEST_ROUND_DAYS + 5):
        add_challenge(day(-offset))
    seen = []
    monkeypatch.setattr(app_module.random, 'choice', lambda seq: seen.append(len(seq)) or seq[0])
    with app_module.app.test_request_context():
        app_module.pick_guest_round('English')
    assert seen == [app_module.GUEST_ROUND_DAYS]
