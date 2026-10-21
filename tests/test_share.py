"""Sharing a finished quiz through a public, unguessable link."""
import re

from conftest import answers, play_quiz
from models import QuizSession


def share_url(html):
    return re.search(r'id="shareUrl" value="([^"]+)"', html).group(1)


def test_results_page_offers_a_share_link(login, app_ctx):
    html = play_quiz(login('Phoenix'), answers(2, 3))
    url = share_url(html)
    code = QuizSession.query.one().share_code
    assert code and url.endswith(f'/r/{code}') and url.startswith('http')
    assert 'I spotted 2 of 3 fake facts about' in html


def test_share_code_is_stable(login, app_ctx):
    client = login('Phoenix')
    first = share_url(play_quiz(client, answers(2, 3)))
    assert share_url(client.get('/results').get_data(as_text=True)) == first


def test_shared_page_is_public_and_shows_the_result(login, app):
    url = share_url(play_quiz(login('Phoenix'), answers(2, 3)))
    html = app.test_client().get(url.split('localhost')[-1]).get_data(as_text=True)
    assert 'Phoenix spotted 2 of 3 fake facts' in html
    assert '<meta property="og:title" content="Phoenix spotted 2 of 3 fake facts">' in html
    assert 'class="review-round"' not in html  # the answers stay private


def test_unknown_or_unfinished_quiz_is_not_found(login, app, app_ctx):
    client = login('Phoenix')
    client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '1', 'language': 'en'})
    quiz = QuizSession.query.one()
    quiz.share_code = 'notfinished'
    from app import db
    db.session.commit()
    assert app.test_client().get('/r/notfinished').status_code == 404
    assert app.test_client().get('/r/doesnotexist').status_code == 404


def test_review_page_can_share_too(login, app_ctx):
    client = login('Phoenix')
    play_quiz(client, answers(1, 3))
    quiz = QuizSession.query.one()
    assert share_url(client.get(f'/review/{quiz.id}').get_data(as_text=True)).endswith(quiz.share_code)


def test_shared_link_dies_with_the_account(login, app, app_ctx):
    from werkzeug.security import generate_password_hash
    from app import db
    from models import User
    client = login('Phoenix')
    url = share_url(play_quiz(client, answers(1, 3)))
    User.query.one().pin_hash = generate_password_hash('1234')
    db.session.commit()
    client.post('/account/delete', data={'pin': '1234', 'confirm': '1'})
    assert app.test_client().get(url.split('localhost')[-1]).status_code == 404
