"""The quiz page shows each answer's result on the round itself (FAKE / TRUE stamps, Finn's reaction): it sends the
answer in the background and gets JSON back. Without JavaScript the form still posts and redirects as before."""
from app import db
from models import QuizSession, Score

FETCH = {'X-Requested-With': 'fetch'}
START = {'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'}


def start(client, **extra):
    client.post('/start_quiz', data={**START, **extra})


def answer(client, option=None, **form):
    data = {**({'option': option} if option else {}), **form}
    return client.post('/quiz', data=data, headers=FETCH)


def test_a_right_answer_comes_back_as_json(login, app_ctx):
    client = login('Ann')
    start(client)
    response = answer(client, 'R0 FAKE')
    assert response.status_code == 200
    assert response.get_json() == {'correct': True, 'timed_out': False, 'fake': 'R0 FAKE',
                                   'message': 'Correct! You found the fake fact.', 'next': '/quiz'}
    assert QuizSession.query.one().score == 1


def test_a_wrong_answer_names_the_fake(login, app_ctx):
    client = login('Ann')
    start(client)
    data = answer(client, 'R0 true A').get_json()
    assert data['correct'] is False and data['fake'] == 'R0 FAKE' and 'R0 FAKE' in data['message']
    assert QuizSession.query.one().score == 0


def test_the_result_isnt_repeated_as_a_message_on_the_next_page(login):
    client = login('Ann')
    start(client)
    answer(client, 'R0 FAKE')
    assert 'You found the fake fact' not in client.get('/quiz').get_data(as_text=True)


def test_the_last_answer_points_to_the_results_and_saves_the_score(login, app_ctx):
    client = login('Ann')
    start(client)
    for i in range(2):
        assert answer(client, f'R{i} FAKE').get_json()['next'] == '/quiz'
    assert answer(client, 'R2 true B').get_json()['next'] == '/results'
    score = Score.query.one()
    assert (score.score, score.total_questions) == (2, 3)
    assert 'Game Results' in client.get('/results').get_data(as_text=True)


def test_no_option_is_an_error_not_an_answer(login, app_ctx):
    client = login('Ann')
    start(client)
    response = answer(client)
    assert response.status_code == 400 and 'select an option' in response.get_json()['error']
    assert QuizSession.query.one().current_question_index == 0


def test_running_out_of_time_is_revealed_too(login, app_ctx):
    client = login('Ann')
    start(client, timed='1')
    client.get('/quiz')  # The round's clock starts when it's shown
    data = answer(client, timeout='1').get_json()
    assert data['timed_out'] is True and data['correct'] is False and data['fake'] == 'R0 FAKE'


def test_a_round_answered_twice_just_moves_on(login, app_ctx):
    client = login('Ann')
    start(client)
    answer(client, 'R0 FAKE')
    quiz = QuizSession.query.one()
    quiz.current_question_index = 0  # As if a second request arrived for the round already claimed
    db.session.commit()
    import app as app_module
    original = app_module.claim_round
    app_module.claim_round = lambda quiz_session, index: False
    try:
        assert answer(client, 'R0 FAKE').get_json() == {'next': '/quiz'}
    finally:
        app_module.claim_round = original


def test_without_javascript_the_form_still_redirects_with_a_message(login):
    client = login('Ann')
    start(client)
    response = client.post('/quiz', data={'option': 'R0 FAKE'})
    assert response.status_code == 302 and response.headers['Location'].endswith('/quiz')
    assert 'You found the fake fact' in client.get('/quiz').get_data(as_text=True)


def test_the_quiz_page_has_finn_and_the_reveal(login):
    client = login('Ann')
    start(client)
    html = client.get('/quiz').get_data(as_text=True)
    assert 'id="quizFinn"' in html and 'class="finn"' in html and 'id="quizReveal"' in html
    assert 'data-fake-label="Fake"' in html and 'data-true-label="True"' in html


def test_the_loading_screen_has_finn(login):
    html = login('Ann').get('/setup').get_data(as_text=True)
    assert 'class="loading-finn"' in html
