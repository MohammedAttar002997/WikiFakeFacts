"""Playing a quiz, the answer review, quiz history and reviewing past quizzes."""
import json
import re

from conftest import answers, play_quiz
from models import QuizSession, Score, User


def test_quiz_scores_and_saves_result(login, app_ctx):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    score = Score.query.one()
    assert (score.score, score.total_questions, round(score.percentage)) == (2, 3, 67)
    assert score.quiz_session_id is not None


def test_results_show_round_by_round_review(login):
    html = play_quiz(login('Player'), ['R0 FAKE', 'R1 true A', 'R2 true C'])
    assert html.count('class="review-round"') == 3
    assert html.count('review-chip-good') == 1
    assert html.count('review-chip-bad') == 2
    assert html.count('Why round') == 3
    assert html.count('is-wrong-pick') == 2


def test_quiz_session_uses_number_of_rounds_returned(login, app_ctx):
    play_quiz(login('Player'), answers(3, 3), length='1')
    assert QuizSession.query.one().total_questions == 3


def test_history_lists_quizzes_newest_first_with_chart_data(login):
    client = login('Player')
    play_quiz(client, answers(1, 3))
    play_quiz(client, answers(3, 3))
    html = client.get('/history').get_data(as_text=True)
    assert html.count('history-row') == 2
    points = json.loads(re.search(r'const points = (\[.*?\]);', html).group(1))
    assert [p['pct'] for p in points] == [33, 100]  # chart is oldest first
    assert html.index('100%') < html.index('33%')  # list is newest first


def test_history_empty_state(login):
    assert 'No quizzes yet' in login('Nobody').get('/history').get_data(as_text=True)


def test_review_is_private_and_needs_a_finished_quiz(login, app_ctx):
    owner = login('Owner')
    play_quiz(owner, answers(3, 3))
    quiz_id = QuizSession.query.one().id

    assert owner.get(f'/review/{quiz_id}').status_code == 200
    assert login('Other').get(f'/review/{quiz_id}').headers['Location'] == '/history'
    assert login('Other', create=False).application.test_client().get(f'/review/{quiz_id}').headers['Location'] == '/'
    assert owner.get('/review/999999').headers['Location'] == '/history'

    owner.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '1', 'language': 'en'})
    unfinished = QuizSession.query.filter_by(is_completed=False).one().id
    assert owner.get(f'/review/{unfinished}').headers['Location'] == '/history'


def test_pages_require_sign_in(app):
    client = app.test_client()
    for path in ('/history', '/analysis', '/custom_quiz'):
        assert client.get(path).status_code == 302
