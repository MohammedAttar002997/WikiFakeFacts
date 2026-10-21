"""My Progress: saved AI analysis, privacy, and "Practice your weak spot"."""
import re

from app import db
from conftest import answers, play_quiz
from models import CustomContent, QuizSession, Score, User


def add_score(user_name, topic, score, total=5, difficulty='Medium (Ages 13-17)'):
    user = User.query.filter_by(username=user_name).one()
    db.session.add(Score(user_id=user.id, topic=topic, difficulty=difficulty, score=score, total_questions=total,
                         percentage=score / total * 100))
    db.session.commit()


def test_analysis_is_saved_and_reused(login, ai):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    client.get('/analysis')
    client.get('/analysis')
    assert len(ai.analysis_calls) == 1


def test_analysis_regenerates_after_new_score_or_language(login, ai, app_ctx):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    client.get('/analysis')
    with client.session_transaction() as sess:
        sess['language'] = 'de'
    client.get('/analysis')
    assert ai.analysis_calls[-1] == 'Deutsch (German)'
    add_score('Player', 'Mars', 1)
    client.get('/analysis')
    assert len(ai.analysis_calls) == 3


def test_analysis_falls_back_to_saved_when_ai_fails(login, ai, app_ctx):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    client.get('/analysis')
    add_score('Player', 'Mars', 1)
    ai.analysis_error = RuntimeError('OpenAI down')
    assert 'Summary in English' in client.get('/analysis').get_data(as_text=True)


def test_analysis_is_private(login, app_ctx):
    login('Other')
    other_id = User.query.filter_by(username='Other').one().id
    client = login('Player')
    play_quiz(client, answers(1, 3))
    assert client.get(f'/analysis/{other_id}').headers['Location'] == '/analysis'


def test_practice_picks_weakest_topic_at_last_difficulty(login, ai, app_ctx):
    client = login('Player')
    add_score('Player', 'Venus', 4)
    add_score('Player', 'Mars', 1, difficulty='Hard (Ages 18-99)')
    add_score('Player', 'Old Upload', 0)  # custom topic with no saved content: skipped
    html = client.get('/analysis').get_data(as_text=True)
    card = html.split('practice-card')[1]
    assert re.search(r'text-truncate">([^<]+)</div>', card).group(1) == 'Mars'

    assert client.post('/practice').headers['Location'] == '/quiz'
    assert (ai.quiz_calls[-1]['topic'], ai.quiz_calls[-1]['difficulty']) == ('Mars', 'Hard (Ages 18-99)')
    assert QuizSession.query.order_by(QuizSession.id.desc()).first().total_questions == 5


def test_practice_reuses_saved_custom_content(login, ai, app_ctx):
    client = login('Player')
    add_score('Player', 'Mars', 3)
    add_score('Player', 'My Notes', 0)
    user = User.query.filter_by(username='Player').one()
    db.session.add(CustomContent(user_id=user.id, title='My Notes', content='Saved notes text', content_type='url'))
    db.session.commit()
    client.post('/practice')
    assert ai.quiz_calls[-1]['custom_content'] == 'Saved notes text'
    assert QuizSession.query.order_by(QuizSession.id.desc()).first().topic == 'My Notes'


def test_practice_without_scores_redirects_to_setup(login):
    assert login('Fresh').post('/practice').headers['Location'] == '/setup'
