"""Finn, the help chat: the feature guide stays true to the app, the player summary, and the buttons he may offer."""
from types import SimpleNamespace

import pytest

import app as app_module
import chatbot as rules
import help_guide
import leagues
from conftest import answers, play_quiz
from views import help_chat


def entries():
    return help_chat.guide_entries()


# ---------- The guide ---------------------------------------------------------------------------------------------

def test_every_page_in_the_guide_exists(app):
    adapter = app.url_map.bind('localhost')
    for feature in entries():
        endpoint, _args = adapter.match(feature.url, method='GET')  # Raises for a page that doesn't exist
        assert endpoint, feature


def test_quick_help_points_at_pages_of_the_guide():
    assert {q['url'] for q in rules.QUICK_HELP} <= help_guide.known_urls(entries())


def test_the_guide_takes_its_numbers_from_the_settings():
    text = help_guide.guide_text(entries())
    assert f'{app_module.TIMED_ROUND_SECONDS} seconds per round' in text
    assert f'Your {app_module.EVENT_BEST_OF} best event quizzes' in text
    assert f'up to {app_module.MAX_FILE_MB} MB' in text
    assert all(tier in text for tier in leagues.TIERS) and f'up to {leagues.GROUP_SIZE} players' in text
    assert 'Hard 20' in text


def test_the_guide_covers_the_features_people_ask_about():
    text = help_guide.guide_text(entries())
    for page in ('/daily', '/league', '/challenges', '/join', '/fakes', '/friends', '/custom_quiz', '/embed', '/try'):
        assert f'({page})' in text


def test_finn_is_told_not_to_give_answers_away():
    prompt = rules.system_prompt('- guide', 'English')
    assert 'Never say which statement is fake' in prompt and "isn't signed in" in prompt


# ---------- The player summary --------------------------------------------------------------------------------------

SUMMARY = {'name': 'Ann', 'streak': 3, 'best_streak': 5, 'games': 12, 'recent': [('Cat', 4, 5)],
           'weakest': ['Saturn'], 'league': {'tier': 'Silver', 'rank': 2, 'size': 9, 'points': 120},
           'daily_done': False, 'daily_topic': 'Saxophone'}


def test_describe_player():
    text = rules.describe_player(SUMMARY)
    assert 'Ann' in text and '3 days in a row (best 5)' in text and 'Cat 4/5' in text and 'Saturn' in text
    assert 'Silver, place 2 of 9, 120 points' in text and 'Saxophone' in text and 'not played yet' in text
    assert rules.describe_player(None) == ''
    assert "Not in this week's league" in rules.describe_player({**SUMMARY, 'league': None, 'recent': [], 'weakest': []})


def test_weakest_topics_need_a_few_games_and_a_low_average():
    rows = [('Cat', 20), ('Cat', 40), ('Dog', 10), ('Saturn', 60), ('Saturn', 50), ('Tea', 90), ('Tea', 80)]
    assert help_chat.weakest_topics(rows) == ['Cat', 'Saturn']  # Dog: one game; Tea: doing fine


# ---------- Buttons -------------------------------------------------------------------------------------------------

def action(label, url):
    return SimpleNamespace(label=label, url=url)


def test_buttons_go_only_to_pages_of_the_guide_and_at_most_two():
    allowed = {'/daily', '/league', '/setup'}
    kept = rules.clean_actions([action('Daily', '/daily'), action('Evil', 'https://evil.example'),
                                action('Nope', '/admin'), action('Again', '/daily'), action('League', '/league?x=1'),
                                action('Setup', '/setup')], allowed)
    assert kept == [{'label': 'Daily', 'url': '/daily'}, {'label': 'League', 'url': '/league?x=1'}]
    assert rules.clean_actions([action('', '/daily')], allowed) == []


# ---------- The endpoint and page -------------------------------------------------------------------------------------

@pytest.fixture
def finn(monkeypatch):
    calls = []

    def respond(message, history, language='English', guide='', player_text=''):
        calls.append({'guide': guide, 'player': player_text})
        return SimpleNamespace(message='Try the daily challenge!',
                               actions=[action('Daily', '/daily'), action('Bad', 'javascript:alert(1)')])
    monkeypatch.setattr(app_module, 'get_chatbot_response', respond)
    return calls


def test_a_signed_in_player_gets_personal_answers(login, finn):
    client = login('Ann')
    play_quiz(client, answers(2, 3))
    reply = client.post('/api/chatbot', json={'message': 'What should I play?'}).get_json()
    assert reply == {'message': 'Try the daily challenge!', 'actions': [{'label': 'Daily', 'url': '/daily'}]}
    assert 'Name: Ann' in finn[-1]['player'] and 'Quizzes finished: 1' in finn[-1]['player']
    assert "This week's league: Bronze, place 1 of 1" in finn[-1]['player']
    assert '(/daily)' in finn[-1]['guide']


def test_guests_get_no_player_details(app, finn):
    app.test_client().post('/api/chatbot', json={'message': 'hi'})
    assert finn[-1]['player'] == ''


def test_the_page_shows_the_conversation_so_far(app, finn):
    client = app.test_client()
    client.post('/api/chatbot', json={'message': 'How do leagues work?'})
    html = client.get('/chatbot').get_data(as_text=True)
    assert html.count('How do leagues work?') >= 2  # Quick help chip and the earlier message
    assert 'Try the daily challenge!' in html
    assert 'Try the daily challenge!' not in app.test_client().get('/chatbot').get_data(as_text=True)
