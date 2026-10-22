"""Every state-changing request needs the session's CSRF token."""
import re

import pytest

import app as app_module
from models import CustomContent, User

TOKEN_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(autouse=True)
def csrf_on(app, monkeypatch):
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)


def token_from(client, path):
    return TOKEN_FIELD.search(client.get(path).get_data(as_text=True)).group(1)


@pytest.mark.parametrize('path, form', [
    ('/setup', {'username': 'Player', 'pin': '1234'}),
    ('/start_quiz', {'category': '1', 'length': '1', 'difficulty': '1'}),
    ('/practice', {}),
    ('/daily/start', {}),
    ('/uploads/1/delete', {}),
])
def test_posts_without_token_are_rejected(login, path, form):
    client = login('Player')
    response = client.post(path, data=form)
    assert response.status_code == 302
    assert "couldn" in client.get(response.headers['Location']).get_data(as_text=True)


def test_rejected_form_shows_message_and_changes_nothing(app, app_ctx):
    client = app.test_client()
    response = client.post('/setup', data={'username': 'Player', 'pin': '1234'},
                           headers={'Referer': 'http://localhost/'})
    assert response.headers['Location'] == '/'
    assert User.query.count() == 0
    assert "couldn" in client.get('/').get_data(as_text=True)


def test_rejected_form_never_redirects_off_site(app):
    response = app.test_client().post('/setup', data={}, headers={'Referer': 'https://evil.example/page'})
    assert response.headers['Location'] == '/'


def test_form_with_token_is_accepted(app, app_ctx):
    client = app.test_client()
    token = token_from(client, '/')
    client.post('/setup', data={'username': 'Player', 'pin': '1234', 'csrf_token': token})
    assert User.query.filter_by(username='Player').count() == 1


def test_delete_upload_needs_token(login, app_ctx):
    from app import db
    client = login('Owner')
    owner = User.query.filter_by(username='Owner').one()
    db.session.add(CustomContent(user_id=owner.id, title='Notes', content='x', content_type='url'))
    db.session.commit()
    upload_id = CustomContent.query.one().id
    client.post(f'/uploads/{upload_id}/delete')
    assert CustomContent.query.count() == 1
    client.post(f'/uploads/{upload_id}/delete', data={'csrf_token': token_from(client, '/custom_quiz')})
    assert CustomContent.query.count() == 0


def test_chatbot_api_needs_token_header(app, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(app_module, 'get_chatbot_response',
                        lambda message, history, language: SimpleNamespace(message='hi', suggested_action=None, action_url=None))
    client = app.test_client()
    rejected = client.post('/api/chatbot', json={'message': 'hello'})
    assert rejected.status_code == 400 and 'error' in rejected.get_json()
    token = re.search(r'name="csrf-token" content="([^"]+)"', client.get('/chatbot').get_data(as_text=True)).group(1)
    accepted = client.post('/api/chatbot', json={'message': 'hello'}, headers={'X-CSRFToken': token})
    assert accepted.status_code == 200


@pytest.mark.parametrize('path', ['/', '/setup', '/custom_quiz', '/daily', '/analysis'])
def test_every_post_form_carries_a_token(login, app_ctx, path):
    from app import db
    from models import Score
    client = login('Player')
    if path == '/analysis':  # My Progress needs a score to show its practice form
        user = User.query.filter_by(username='Player').one()
        db.session.add(Score(user_id=user.id, topic='Cat', difficulty='Easy (Ages 7-12)', score=1, total_questions=3,
                             percentage=33.3))
        db.session.commit()
    html = client.get(path).get_data(as_text=True)
    forms = re.findall(r'<form\b[^>]*method="post"[^>]*>.*?</form>', html, re.I | re.S)
    assert forms, f'no POST form found on {path}'
    assert all('name="csrf_token"' in form for form in forms)
