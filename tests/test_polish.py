"""Finishing touches from the final check-up: error pages, headers, the help assistant's language, navigation and
page titles."""
import io
import os
from types import SimpleNamespace

import pytest

import app as app_module
from app import db
from conftest import answers, play_quiz
from models import UserBadge, User
from translations import translate


def test_pages_send_security_headers(app):
    response = app.test_client().get('/')
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Content-Security-Policy'] == "frame-ancestors 'none'"
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'


# ---------- Error pages --------------------------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/no-such-page', '/players/999', '/r/no-such-code'])
def test_missing_pages_get_a_translated_page(login, path):
    response = login('Player', language='fr').get(path)
    html = response.get_data(as_text=True)
    assert response.status_code == 404
    assert translate('Page not found', 'fr') in html and 'wf-navbar' in html  # The site's own page, in French


def test_server_errors_get_a_friendly_page(login, monkeypatch, app):
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    monkeypatch.setattr(app_module, 'recommend_difficulty', lambda user: 1 / 0)
    response = login('Player', language='es').get('/setup')
    assert response.status_code == 500
    assert translate('Something went wrong', 'es') in response.get_data(as_text=True)


def test_too_large_upload_goes_back_with_a_message(login, app, monkeypatch):
    monkeypatch.setitem(app.config, 'MAX_CONTENT_LENGTH', 1000)
    client = login('Player')
    response = client.post('/account/photo', data={'photo': (io.BytesIO(b'x' * 5000), 'big.png')},
                           content_type='multipart/form-data', headers={'Referer': 'http://localhost/account'})
    assert response.headers['Location'] == '/account'
    assert 'too big' in client.get('/account').get_data(as_text=True)


# ---------- Help assistant -------------------------------------------------------------------------------------------

@pytest.fixture
def assistant(monkeypatch):
    calls = []

    def respond(message, history, language='English'):
        calls.append(language)
        return SimpleNamespace(message='Hola', suggested_action='Ir', action_url=calls_url.pop(0))
    calls_url = []
    monkeypatch.setattr(app_module, 'get_chatbot_response', respond)
    return calls, calls_url


def test_assistant_answers_in_the_interface_language(login, assistant):
    calls, urls = assistant
    urls.append('/setup')
    login('Player', language='es').post('/api/chatbot', json={'message': 'hola'})
    assert calls == ['Español (Spanish)']


@pytest.mark.parametrize('url, expected', [
    ('/setup', '/setup'), ('javascript:alert(1)', None), ('https://evil.example', None), ('//evil.example', None),
])
def test_assistant_links_stay_on_this_site(app, app_ctx, assistant, url, expected):
    _calls, urls = assistant
    urls.append(url)
    assert app.test_client().post('/api/chatbot', json={'message': 'hi'}).get_json()['action_url'] == expected


def test_quick_help_is_translated(login):
    html = login('Player', language='de').get('/chatbot').get_data(as_text=True)
    assert translate('How do I start playing?', 'de') in html and 'How do I start playing?' not in html


def test_assistant_failures_show_a_translated_message(login, monkeypatch):
    def broken(message, history, language):
        raise RuntimeError('upstream says no')
    monkeypatch.setattr(app_module, 'get_chatbot_response', broken)
    response = login('Player', language='it').post('/api/chatbot', json={'message': 'ciao'})
    assert response.status_code == 500
    assert response.get_json()['error'] == translate('Sorry, I encountered an error. Please try again.', 'it')


# ---------- Navigation, titles, layout -------------------------------------------------------------------------------

def test_signed_in_players_can_reach_their_profile_from_the_navbar(login, app):
    html = login('Player').get('/leaderboard').get_data(as_text=True)
    assert 'class="nav-avatar' in html and html.count('href="/account"') >= 2  # Photo (wide) and menu item (phone)
    assert 'class="nav-avatar' not in app.test_client().get('/leaderboard').get_data(as_text=True)


@pytest.mark.parametrize('path, title', [('/leaderboard', 'Leaderboard'), ('/players', 'Players'),
                                         ('/chatbot', 'Help'), ('/daily', 'Daily challenge')])
def test_pages_have_their_own_translated_title(login, path, title):
    html = login('Player', language='pt').get(path).get_data(as_text=True)
    assert f'<title>{translate(title, "pt")} · WikiFakeFact</title>' in html


def test_leaderboard_has_no_empty_filter_slot(app):
    html = app.test_client().get('/leaderboard').get_data(as_text=True)
    assert 'filter-actions' not in html
    assert 'filter-actions' in app.test_client().get('/leaderboard?period=week').get_data(as_text=True)


def test_quiz_page_has_a_main_heading(login, app_ctx):
    client = login('Player')
    client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    assert '<h1 class="h2 mb-1">' in client.get('/quiz').get_data(as_text=True)


def test_players_page_totals(login, app_ctx):
    play_quiz(login('Alice'), answers(3, 3))
    bob = login('Bob')
    play_quiz(bob, answers(1, 3))
    play_quiz(bob, answers(2, 3))
    login('Idle')  # Never played, so not listed
    html = bob.get('/players').get_data(as_text=True)
    assert html.index('Bob') < html.index('Alice')  # Most games first
    assert '2 games played' in html and '1 game played' in html and 'Idle' not in html
    badges = UserBadge.query.filter_by(user_id=User.query.filter_by(username='Bob').one().id).count()
    assert f'🏅 {badges}' in html


# ---------- Static files -------------------------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/static/css/app.css', '/static/js/app.js'])
def test_static_files_come_from_public(app, path):
    # On Vercel the CDN serves public/ at the same URLs, so pages must link there and Flask must serve it locally
    assert os.path.isfile(os.path.join(os.path.dirname(app_module.__file__), 'public', path.lstrip('/')))
    assert path in app.test_client().get('/').get_data(as_text=True)
    assert app.test_client().get(path).status_code == 200
