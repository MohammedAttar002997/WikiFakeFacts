"""Recommended difficulty from a player's recent quizzes."""
from datetime import timedelta

import pytest

import app as app_module
from app import db
from models import Score, User, utcnow

EASY, MEDIUM, HARD = 'Easy (Ages 7-12)', 'Medium (Ages 13-17)', 'Hard (Ages 18-99)'


def add_scores(user_name, results):
    """results: (percentage, difficulty) oldest first."""
    user = User.query.filter_by(username=user_name).one()
    for i, (pct, difficulty) in enumerate(results):
        db.session.add(Score(user_id=user.id, topic='Cat', difficulty=difficulty, score=round(pct / 20),
                             total_questions=5, percentage=pct, timestamp=utcnow() - timedelta(hours=len(results) - i)))
    db.session.commit()
    return user


@pytest.mark.parametrize('results, expected', [
    ([(90, MEDIUM)] * 3, ('3', 'up')),                         # doing well on Medium: try Hard
    ([(20, MEDIUM)] * 3, ('1', 'down')),                       # struggling on Medium: try Easy
    ([(60, MEDIUM)] * 3, ('2', 'stay')),
    ([(95, HARD)] * 3, ('3', 'stay')),                         # already at the top
    ([(10, EASY)] * 3, ('1', 'stay')),                         # already at the bottom
    ([(10, MEDIUM)] * 5 + [(90, MEDIUM)] * 5, ('3', 'up')),    # only the last 5 quizzes count
    ([(90, EASY), (90, EASY), (90, MEDIUM)], ('3', 'up')),     # steps up from the latest difficulty
])
def test_recommendation(login, app_ctx, results, expected):
    login('Player')
    user = add_scores('Player', results)
    recommendation = app_module.recommend_difficulty(user)
    assert (recommendation['id'], recommendation['direction']) == expected


def test_no_recommendation_before_three_quizzes(login, app_ctx):
    login('Player')
    user = add_scores('Player', [(90, MEDIUM)] * 2)
    assert app_module.recommend_difficulty(user) is None


def test_setup_preselects_and_explains_the_recommendation(login, app_ctx):
    client = login('Player')
    add_scores('Player', [(90, MEDIUM)] * 3)
    html = client.get('/setup').get_data(as_text=True)
    assert 'id="diff3" value="3" required checked' in html
    assert 'Recommended' in html and 'try a harder level' in html


def test_new_players_get_medium_without_a_badge(login):
    html = login('Fresh').get('/setup').get_data(as_text=True)
    assert 'id="diff2" value="2" required checked' in html
    assert 'tile-badge' not in html


def test_custom_quiz_page_uses_it_too(login, app_ctx):
    client = login('Player')
    add_scores('Player', [(20, MEDIUM)] * 3)
    html = client.get('/custom_quiz').get_data(as_text=True)
    assert 'id="diff1" value="1" required checked' in html
