"""The error log: unexpected errors are kept in the database, since Vercel's own logs are gone within minutes."""
import pytest
from sqlalchemy.exc import IntegrityError

import app as app_module
from conftest import answers, play_quiz
from migrate_production import use_production_database
from models import ErrorLog, User


def test_unhandled_errors_are_saved(login, monkeypatch, app, app_ctx):
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    monkeypatch.setattr(app_module, 'recommend_difficulty', lambda user: 1 / 0)
    assert login('Player').get('/setup').status_code == 500
    entry = ErrorLog.query.one()
    assert (entry.method, entry.path) == ('GET', '/setup')
    assert entry.error == 'ZeroDivisionError: division by zero'
    assert 'recommend_difficulty' in entry.traceback or '<lambda>' in entry.traceback


def test_failed_quiz_creation_is_saved(login, monkeypatch, app_ctx):
    def fail(*args, **kwargs):
        raise RuntimeError('OpenAI down')
    monkeypatch.setattr(app_module, 'get_ai_response', fail)
    client = login('Player')
    response = client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '1', 'language': 'en'})
    assert response.headers['Location'] == '/setup'
    entry = ErrorLog.query.one()
    assert (entry.method, entry.path, entry.error) == ('POST', '/start_quiz', 'RuntimeError: OpenAI down')


def test_failed_analysis_is_saved_and_the_page_still_works(login, ai, app_ctx):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    ai.analysis_error = RuntimeError('OpenAI down')
    assert client.get('/analysis').status_code == 200
    assert ErrorLog.query.one().error == 'RuntimeError: OpenAI down'


def test_only_the_newest_errors_are_kept(login, monkeypatch, app, app_ctx):
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    monkeypatch.setattr(app_module, 'ERROR_LOG_SIZE', 3)
    counter = iter(range(100))

    def fail(user):
        raise ValueError(f'error {next(counter)}')
    monkeypatch.setattr(app_module, 'recommend_difficulty', fail)
    client = login('Player')
    for _ in range(5):
        client.get('/setup')
    assert [e.error for e in ErrorLog.query.order_by(ErrorLog.id)] == [
        'ValueError: error 2', 'ValueError: error 3', 'ValueError: error 4']


def test_database_errors_leave_out_the_values():
    error = IntegrityError('INSERT INTO user (username) VALUES (?)', ('Secret Name',), Exception('UNIQUE failed'))
    assert app_module.describe_error(error) == 'IntegrityError: UNIQUE failed'


def test_a_broken_error_log_doesnt_hide_the_original_error(login, monkeypatch, app_ctx):
    def fail(*args, **kwargs):
        raise RuntimeError('OpenAI down')
    monkeypatch.setattr(app_module, 'get_ai_response', fail)
    monkeypatch.setattr(app_module, 'describe_error', lambda error: 1 / 0)
    client = login('Player')
    response = client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '1', 'language': 'en'})
    assert response.headers['Location'] == '/setup'
    assert 'create your quiz right now' in client.get('/setup').get_data(as_text=True)
    assert ErrorLog.query.count() == 0
    assert User.query.filter_by(username='Player').one()  # The session still works after the failed save


def test_errors_command_shows_the_newest_first(login, monkeypatch, app, app_ctx):
    runner = app.test_cli_runner()
    assert 'No errors saved.' in runner.invoke(args=['errors']).output
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    monkeypatch.setattr(app_module, 'recommend_difficulty', lambda user: 1 / 0)
    client = login('Player')
    client.get('/setup')
    client.get('/setup?again=1')
    output = runner.invoke(args=['errors', '-n', '1', '--full']).output
    assert output.count('GET /setup') == 1 and 'ZeroDivisionError: division by zero' in output
    assert 'File ' in output  # --full adds the traceback


def test_production_errors_needs_the_settings_file(tmp_path):
    with pytest.raises(SystemExit, match='vercel env pull'):
        use_production_database(str(tmp_path / 'missing.env'))
