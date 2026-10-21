"""Shared fixtures. Tests run against a throwaway SQLite database built by the real migrations, and every AI or
network call is stubbed, so the suite is free and offline."""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Must be set before the app is imported: the database URL is read at import time
_db_dir = tempfile.mkdtemp(prefix='wikifakefacts-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_db_dir, 'test.sqlite').replace('\\', '/')

import app as app_module  # noqa: E402
from app import app as flask_app, db, init_database  # noqa: E402
from data import AIAnalysis  # noqa: E402
from models import User  # noqa: E402

flask_app.config.update(TESTING=True)
with flask_app.app_context():
    init_database()


def fake_quiz(n, topic, difficulty, language, custom_content=None):
    """n rounds where 'R{i} FAKE' is the fake fact; mirrors get_ai_response's return value."""
    rounds = [{f'R{i} true A': True, f'R{i} FAKE': False, f'R{i} true B': True, f'R{i} true C': True} for i in range(n)]
    return rounds, 'Stub', 'Stub Topic', [f'Why round {i} is fake.' for i in range(n)]


def fake_analysis(history_json, language):
    return AIAnalysis(overall_summary=f'Summary in {language}', strongest_area='Strong', weakest_area='Weak',
                      learning_path=['Step one'], topic_insights=[])


class AIStub:
    """Records calls and lets a test swap behaviour (e.g. make the analysis fail)."""

    def __init__(self):
        self.quiz_calls = []
        self.analysis_calls = []
        self.analysis_error = None

    def quiz(self, n, topic, difficulty, language, custom_content=None):
        self.quiz_calls.append({'n': n, 'topic': topic, 'difficulty': difficulty, 'language': language,
                                'custom_content': custom_content})
        return fake_quiz(n, topic, difficulty, language, custom_content)

    def analysis(self, history_json, language):
        if self.analysis_error:
            raise self.analysis_error
        self.analysis_calls.append(language)
        return fake_analysis(history_json, language)


@pytest.fixture(autouse=True)
def ai(monkeypatch):
    stub = AIStub()
    monkeypatch.setattr(app_module, 'get_ai_response', stub.quiz)
    monkeypatch.setattr(app_module, 'get_ai_analysis', stub.analysis)
    monkeypatch.setattr(app_module, 'extract_text_from_url', lambda url: 'Volcanoes erupt magma. ' * 60)
    return stub


@pytest.fixture(autouse=True)
def clean_db():
    """Empty every table (keeping the migration version) so each test starts fresh."""
    yield
    with flask_app.app_context():
        db.session.remove()
        with db.engine.begin() as conn:
            for table in reversed(db.metadata.sorted_tables):
                conn.execute(table.delete())
    app_module.FAILED_PINS.clear()


@pytest.fixture
def app():
    return flask_app


@pytest.fixture
def app_ctx():
    with flask_app.app_context():
        yield


@pytest.fixture
def make_user():
    def make(name='Tester'):
        with flask_app.app_context():
            user = User(username=name)
            db.session.add(user)
            db.session.commit()
            return user.id
    return make


@pytest.fixture
def login(make_user):
    """A test client signed in as `name` (created if needed), with an interface language."""
    def make(name='Tester', language='en', create=True):
        if create:
            with flask_app.app_context():
                if not User.query.filter_by(username=name).first():
                    make_user(name)
        client = flask_app.test_client()
        with client.session_transaction() as sess:
            sess['username'] = name
            sess['language'] = language
        return client
    return make


def play_quiz(client, picks, category='1', length='1', difficulty='2'):
    """Start a normal quiz and answer it with `picks` (statements); returns the results page HTML."""
    client.post('/start_quiz', data={'category': category, 'length': length, 'difficulty': difficulty,
                                     'language': 'en'})
    for pick in picks:
        client.post('/quiz', data={'option': pick})
    return client.get('/results').get_data(as_text=True)


def answers(right, total):
    """Picks that get the first `right` of `total` rounds correct."""
    return [f'R{i} FAKE' if i < right else f'R{i} true A' for i in range(total)]
