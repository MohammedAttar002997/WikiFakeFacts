"""Finn beyond the quiz: the results as a case file, the help chat's face, and empty and error pages."""
import pytest

from conftest import answers, play_quiz
from models import QuizSession


@pytest.mark.parametrize('right, mood, stamp', [(3, 'mood-happy', 'Case solved'), (2, None, 'Case closed'),
                                                (0, 'mood-sad', 'They got away')])
def test_results_are_a_case_file_with_finns_verdict(login, app_ctx, right, mood, stamp):
    html = play_quiz(login('Ann'), answers(right, 3))
    quiz = QuizSession.query.one()
    assert f'Case file No. {quiz.id}' in html and stamp in html and 'case-stamp' in html
    finn = html.split('case-finn')[1].split('</svg>')[0]
    assert ('class="finn ' + mood in finn) if mood else 'class="finn"' in finn


def test_the_help_chat_is_finn(login):
    html = login('Ann').get('/chatbot').get_data(as_text=True)
    assert "I&#39;m Finn" in html or "I'm Finn" in html
    chat = html.split('class="chat-shell"')[1]
    assert chat.count('finn-face') >= 4 and 'bi-robot' not in chat  # Header, greeting and the two templates
    assert 'id="finnAvatar"' in html and 'id="finnThinking"' in html and 'mood-think' in html


def test_the_help_button_shows_finns_face(login):
    html = login('Ann').get('/setup').get_data(as_text=True)
    button = html.split('class="chatbot-fab"')[1].split('</a>')[0]
    assert 'class="finn"' in button and 'viewBox="29 8 84 88"' in button


def test_a_missing_page_shows_finn_searching(login):
    response = login('Ann').get('/no-such-page')
    assert response.status_code == 404 and 'class="finn mood-search"' in response.get_data(as_text=True)


def test_empty_pages_show_finn(login):
    client = login('Ann')
    assert 'class="finn mood-think"' in client.get('/history').get_data(as_text=True)
    assert 'finn-holder' in client.get('/fakes').get_data(as_text=True)


def test_finn_alone_has_no_mood_and_the_whole_picture(login):
    """The plain include (quiz page, loading screen) is unchanged by the new options."""
    html = login('Ann').get('/setup').get_data(as_text=True)
    loading = html.split('class="loading-finn"')[1].split('</svg>')[0]
    assert 'class="finn"' in loading and 'viewBox="0 0 150 172"' in loading
