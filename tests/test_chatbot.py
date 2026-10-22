"""The help chatbot keeps a separate conversation per browser."""
import json
from types import SimpleNamespace

import pytest

import app as app_module
from models import ChatbotSession


@pytest.fixture(autouse=True)
def fake_chatbot(monkeypatch):
    seen = []

    def respond(message, history, language="English"):
        seen.append([m['content'] for m in history])
        return SimpleNamespace(message=f'Reply to {message}', suggested_action=None, action_url=None)
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
