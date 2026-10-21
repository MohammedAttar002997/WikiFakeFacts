"""Custom quizzes: topics, saved uploads, replaying and deleting them."""
import pytest

from app import db
from models import CustomContent, QuizSession, Score, User

FORM = {'length': '1', 'difficulty': '2', 'language': 'en'}


@pytest.fixture
def uploaded(login, app_ctx):
    client = login('Owner')
    client.post('/custom_quiz', data={**FORM, 'title': '', 'url': 'https://example.com/volcanoes'})
    return client, CustomContent.query.one().id


def latest_quiz():
    return QuizSession.query.order_by(QuizSession.id.desc()).first()


def test_custom_quiz_is_labelled_by_ai_topic_and_saved(uploaded):
    _client, upload_id = uploaded
    upload = db.session.get(CustomContent, upload_id)
    assert (upload.title, upload.content_type) == ('Stub Topic', 'url')
    assert latest_quiz().topic == 'Stub Topic'


def test_typed_title_wins_over_ai_topic(login, app_ctx):
    login('Owner').post('/custom_quiz', data={**FORM, 'title': 'Geology notes', 'url': 'https://example.com/v'})
    assert latest_quiz().topic == 'Geology notes'


def test_same_content_is_not_saved_twice(uploaded):
    client, _upload_id = uploaded
    client.post('/custom_quiz', data={**FORM, 'title': '', 'url': 'https://example.com/volcanoes'})
    assert CustomContent.query.count() == 1


def test_uploads_tab_lists_saved_uploads(uploaded, login):
    client, _upload_id = uploaded
    assert 'uploads-tab' in client.get('/custom_quiz').get_data(as_text=True)
    assert 'uploads-tab' not in login('Fresh').get('/custom_quiz').get_data(as_text=True)


def test_replay_uses_saved_content_without_fetching(uploaded, ai, monkeypatch):
    import app as app_module
    client, upload_id = uploaded
    monkeypatch.setattr(app_module, 'extract_text_from_url', lambda url: pytest.fail('should not fetch'))
    response = client.post('/custom_quiz', data={**FORM, 'length': '2', 'title': '', 'saved_content_id': upload_id})
    assert response.headers['Location'] == '/quiz'
    assert ai.quiz_calls[-1]['custom_content'].startswith('Volcanoes')
    assert (latest_quiz().total_questions, latest_quiz().topic) == (5, 'Stub Topic')


def test_other_players_cannot_use_or_delete_an_upload(uploaded, login):
    _client, upload_id = uploaded
    other = login('Other')
    assert other.post('/custom_quiz', data={**FORM, 'saved_content_id': upload_id}).headers['Location'] == '/custom_quiz'
    other.post(f'/uploads/{upload_id}/delete')
    assert CustomContent.query.count() == 1


def test_delete_keeps_scores(uploaded):
    client, upload_id = uploaded
    user = User.query.filter_by(username='Owner').one()
    db.session.add(Score(user_id=user.id, topic='Stub Topic', score=1, total_questions=3, percentage=33.3))
    db.session.commit()
    assert client.post(f'/uploads/{upload_id}/delete').headers['Location'] == '/custom_quiz?tab=uploads'
    assert (CustomContent.query.count(), Score.query.count()) == (0, 1)
