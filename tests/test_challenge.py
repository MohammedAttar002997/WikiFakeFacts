"""Head-to-head challenges: play the exact questions of a shared quiz and compare scores."""
import json

import pytest

import app as app_module
from app import db
from conftest import answers, play_quiz
from models import QuizSession, User


@pytest.fixture
def shared(login, app_ctx):
    """Phoenix finished a 3-round quiz with 2 right; returns its share code."""
    play_quiz(login('Phoenix'), answers(2, 3))
    return QuizSession.query.one().share_code  # created when the results page was shown


def take(client, code, picks):
    client.post(f'/r/{code}/play')
    for pick in picks:
        client.post('/quiz', data={'option': pick})
    return client.get('/results').get_data(as_text=True)


def test_challenger_plays_the_exact_same_questions(login, shared, ai):
    calls_before = len(ai.quiz_calls)
    take(login('Alice'), shared, answers(3, 3))
    original = QuizSession.query.filter_by(share_code=shared).one()
    attempt = QuizSession.query.filter_by(challenge_of_id=original.id).one()
    assert json.loads(attempt.questions_json) == json.loads(original.questions_json)
    assert attempt.difficulty == original.difficulty
    assert len(ai.quiz_calls) == calls_before  # no new AI call


@pytest.mark.parametrize('right, headline', [(3, 'You beat Phoenix!'), (2, 'a tie with Phoenix'), (1, 'Phoenix wins this one.')])
def test_results_compare_scores(login, shared, right, headline):
    html = take(login('Alice'), shared, answers(right, 3))
    assert headline in html.replace('&#39;', "'")


def test_shared_page_lists_challengers(login, shared, app):
    take(login('Alice'), shared, answers(3, 3))
    take(login('Bob'), shared, answers(1, 3))
    html = app.test_client().get(f'/r/{shared}').get_data(as_text=True)
    assert html.index('Alice') < html.index('Bob')
    assert 'Beat Phoenix' in html and 'Behind Phoenix' in html


def test_one_attempt_per_challenger(login, shared):
    client = login('Alice')
    take(client, shared, answers(1, 3))
    assert client.post(f'/r/{shared}/play').headers['Location'] == f'/r/{shared}'
    assert QuizSession.query.filter(QuizSession.challenge_of_id.isnot(None)).count() == 1


def test_unfinished_attempt_resumes(login, shared):
    client = login('Alice')
    client.post(f'/r/{shared}/play')
    client.post('/quiz', data={'option': 'R0 FAKE'})
    client.post(f'/r/{shared}/play')
    attempt = QuizSession.query.filter(QuizSession.challenge_of_id.isnot(None)).one()
    assert attempt.current_question_index == 1


def test_owner_is_sent_to_their_review(login, shared):
    response = login('Phoenix', create=False).post(f'/r/{shared}/play')
    assert '/review/' in response.headers['Location']


def test_signed_out_visitor_signs_in_then_returns(app, shared, app_ctx):
    client = app.test_client()
    assert client.post(f'/r/{shared}/play').headers['Location'] == '/'
    response = client.post('/setup', data={'username': 'Newcomer', 'pin': '1234'})
    assert response.headers['Location'] == f'/r/{shared}'


def test_daily_challenge_quizzes_cannot_be_replayed(login, app_ctx):
    client = login('Phoenix')
    client.post('/daily/start')
    for i in range(app_module.DAILY_ROUNDS):
        client.post('/quiz', data={'option': f'R{i} FAKE'})
    client.get('/results')  # creates the share link
    code = QuizSession.query.one().share_code
    assert login('Alice').post(f'/r/{code}/play').headers['Location'] == '/daily'


def test_attempts_survive_the_original_player_deleting_their_account(login, shared):
    take(login('Alice'), shared, answers(3, 3))
    phoenix = User.query.filter_by(username='Phoenix').one()
    app_module.delete_player_data(phoenix)
    attempt = QuizSession.query.one()
    assert attempt.challenge_of_id is None and attempt.is_completed
