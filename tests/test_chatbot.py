"""The help chatbot keeps a separate conversation per browser."""
import json
from types import SimpleNamespace

import pytest

import app as app_module
from models import ChatbotSession


@pytest.fixture(autouse=True)
def fake_chatbot(monkeypatch):
    seen = []

    def respond(message, history, language="English", **context):
        seen.append([m['content'] for m in history])
        return SimpleNamespace(message=f'Reply to {message}', actions=[])
    monkeypatch.setattr(app_module, 'get_chatbot_response', respond)
    return seen


def ask(client, message):
    return client.post('/api/chatbot', json={'message': message})


def test_each_browser_has_its_own_conversation(app, app_ctx, fake_chatbot):
    alice, bob = app.test_client(), app.test_client()
    ask(alice, 'my secret question')
    ask(bob, 'hello')
    assert fake_chatbot[-1] == []  # Bob's chatbot saw none of Alice's messages
    assert ChatbotSession.query.count() == 2


def test_conversation_continues_in_the_same_browser(app, fake_chatbot):
    client = app.test_client()
    ask(client, 'first')
    ask(client, 'second')
    assert fake_chatbot[-1] == ['first', 'Reply to first']


def test_switching_player_starts_a_fresh_conversation(login, fake_chatbot):
    client = login('Player')
    ask(client, 'first')
    client.get('/logout')
    ask(client, 'second')
    assert fake_chatbot[-1] == []


def test_empty_message_is_rejected(app):
    response = ask(app.test_client(), '   ')
    assert response.status_code == 400
    assert 'error' in json.loads(response.data)


# ---------- One conversation per player, even on a shared browser -----------------------------------------------------

def sign_in_form(client, name, pin='1234'):
    """Sign in through the real form, the way a second player on the same browser would (no sign-out first)."""
    return client.post('/setup', data={'username': name, 'pin': pin})


def test_signing_in_as_someone_else_starts_a_fresh_conversation(app, app_ctx, fake_chatbot):
    client = app.test_client()
    sign_in_form(client, 'Tas')
    ask(client, 'my private question')
    sign_in_form(client, 'P8X')  # Straight to another account, no sign-out
    assert 'my private question' not in client.get('/chatbot').get_data(as_text=True)
    ask(client, 'hello')
    assert fake_chatbot[-1] == []  # Finn didn't see Tas's conversation either


def test_a_guest_chat_is_not_shown_after_signing_in(app, app_ctx, fake_chatbot):
    client = app.test_client()
    ask(client, 'guest question')
    sign_in_form(client, 'Tas')
    assert 'guest question' not in client.get('/chatbot').get_data(as_text=True)


def test_the_same_player_keeps_their_conversation(app, app_ctx, fake_chatbot):
    client = app.test_client()
    sign_in_form(client, 'Tas')
    ask(client, 'first question')
    sign_in_form(client, 'Tas')  # Signing in again as yourself
    assert 'first question' in client.get('/chatbot').get_data(as_text=True)
    ask(client, 'second')
    assert fake_chatbot[-1] == ['first question', 'Reply to first question']


def test_a_session_that_ended_without_sign_out_hides_the_chat(app, app_ctx, fake_chatbot):
    client = app.test_client()
    sign_in_form(client, 'Tas')
    ask(client, 'secret')
    with client.session_transaction() as sess:
        sess['pin_token'] = 'stale'  # As after a PIN change on another device: no longer signed in
    assert 'secret' not in client.get('/chatbot').get_data(as_text=True)


def test_signing_in_as_someone_else_forgets_the_current_quiz(app, app_ctx):
    from conftest import answers, play_quiz
    client = app.test_client()
    sign_in_form(client, 'Tas')
    play_quiz(client, answers(1, 3))
    assert client.get('/results').status_code == 200
    sign_in_form(client, 'P8X')
    assert client.get('/results').headers['Location'].endswith('/setup')
