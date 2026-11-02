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


def test_results_compare_answers_round_by_round(login, shared):
    # Phoenix got rounds 1-2 right; Alice gets only round 1 right, picking a true statement in rounds 2-3
    html = take(login('Alice'), shared, answers(1, 3))
    assert 'Round by round: you vs Phoenix' in html
    assert html.count('Picked by Phoenix') == 3
    assert html.count('Your pick') == 3


def test_challenger_review_compares_with_the_original(login, shared):
    client = login('Alice')
    take(client, shared, answers(3, 3))
    attempt = QuizSession.query.filter(QuizSession.challenge_of_id.isnot(None)).one()
    html = client.get(f'/review/{attempt.id}').get_data(as_text=True)
    assert 'you vs Phoenix' in html


def test_owner_compares_with_one_challenger(login, shared):
    take(login('Alice'), shared, answers(3, 3))
    original = QuizSession.query.filter_by(share_code=shared).one()
    attempt = QuizSession.query.filter_by(challenge_of_id=original.id).one()
    phoenix = login('Phoenix', create=False)
    html = phoenix.get(f'/review/{original.id}?vs={attempt.id}').get_data(as_text=True)
    assert 'you vs Alice' in html and 'Picked by Alice' in html
    assert f'vs={attempt.id}' in phoenix.get(f'/r/{shared}').get_data(as_text=True)


def test_review_ignores_a_vs_that_is_not_a_challenger(login, shared):
    play_quiz(login('Alice'), answers(1, 3))  # Alice's own quiz, not an attempt at Phoenix's
    other = QuizSession.query.filter(QuizSession.share_code != shared).one()
    original = QuizSession.query.filter_by(share_code=shared).one()
    html = login('Phoenix', create=False).get(f'/review/{original.id}?vs={other.id}').get_data(as_text=True)
    assert 'Answer review' in html and 'Alice' not in html


def test_bell_counts_new_challengers_until_the_challenges_page_is_seen(login, shared):
    take(login('Alice'), shared, answers(3, 3))
    take(login('Bob'), shared, answers(1, 3))
    phoenix = login('Phoenix', create=False)
    assert '2 new challenge updates' in phoenix.get('/history').get_data(as_text=True)

    html = phoenix.get('/challenges').get_data(as_text=True)
    assert html.index('Bob') < html.index('Alice')  # newest first
    assert html.count('chip-new') == 2
    assert 'new challenge update' not in html  # seen now

    assert 'chip-new' not in phoenix.get('/challenges').get_data(as_text=True)
    take(login('Carol'), shared, answers(2, 3))
    assert '1 new challenge update' in phoenix.get('/history').get_data(as_text=True)


def test_challenges_page_lists_challenges_taken(login, shared):
    alice = login('Alice')
    take(alice, shared, answers(3, 3))
    html = alice.get('/challenges').get_data(as_text=True)
    assert 'Phoenix' in html and 'You won' in html
    assert 'new challenge update' not in html  # taking a challenge isn't a notification for the challenger


def test_challenges_page_needs_sign_in(app):
    assert app.test_client().get('/challenges').headers['Location'] == '/'


def attempt_id():
    return QuizSession.query.filter(QuizSession.challenge_of_id.isnot(None)).one().id


def play_rematch(client, attempt, picks):
    client.post(f'/rematch/{attempt}')
    for pick in picks:
        client.post('/quiz', data={'option': pick})
    return client.get('/results').get_data(as_text=True)


def test_rematch_makes_a_new_quiz_on_the_same_topic_and_sends_it(login, shared, ai):
    alice = login('Alice')
    take(alice, shared, answers(1, 3))
    original = QuizSession.query.filter_by(share_code=shared).one()
    html = play_rematch(alice, attempt_id(), answers(3, 3))
    assert 'Rematch sent to Phoenix!' in html

    rematch = QuizSession.query.filter(QuizSession.rematch_for_id.isnot(None)).one()
    assert (rematch.topic, rematch.difficulty, rematch.total_questions) == (
        original.topic, original.difficulty, original.total_questions)
    assert ai.quiz_calls[-1]['topic'] == original.topic
    assert rematch.rematch_for_id == User.query.filter_by(username='Phoenix').one().id


def test_opponent_sees_the_rematch_and_plays_it(login, shared):
    alice = login('Alice')
    take(alice, shared, answers(1, 3))
    play_rematch(alice, attempt_id(), answers(3, 3))
    rematch = QuizSession.query.filter(QuizSession.rematch_for_id.isnot(None)).one()

    phoenix = login('Phoenix', create=False)
    # Alice taking Phoenix's challenge, then her rematch
    assert '2 new challenge updates' in phoenix.get('/history').get_data(as_text=True)
    html = phoenix.get('/challenges').get_data(as_text=True)
    assert 'Rematches for you' in html and f'/r/{rematch.share_code}/play' in html

    results = take(phoenix, rematch.share_code, answers(2, 3))
    assert 'Alice wins this one.' in results
    assert 'Rematches for you' not in phoenix.get('/challenges').get_data(as_text=True)  # played, so gone
    # Alice is told Phoenix took her rematch, and can rematch back
    alice_page = alice.get('/challenges').get_data(as_text=True)
    assert 'chip-new' in alice_page and 'Rematch Phoenix' in alice_page


def test_unfinished_rematch_is_not_sent(login, shared):
    alice = login('Alice')
    take(alice, shared, answers(1, 3))
    alice.post(f'/rematch/{attempt_id()}')
    alice.post('/quiz', data={'option': 'R0 FAKE'})
    html = login('Phoenix', create=False).get('/challenges').get_data(as_text=True)
    assert 'Rematches for you' not in html


def test_both_players_can_ask_for_a_rematch(login, shared):
    take(login('Alice'), shared, answers(1, 3))
    phoenix = login('Phoenix', create=False)
    play_rematch(phoenix, attempt_id(), answers(3, 3))
    rematch = QuizSession.query.filter(QuizSession.rematch_for_id.isnot(None)).one()
    assert rematch.rematch_for_id == User.query.filter_by(username='Alice').one().id


def test_outsiders_cannot_rematch(login, shared):
    take(login('Alice'), shared, answers(1, 3))
    response = login('Mallory').post(f'/rematch/{attempt_id()}')
    assert response.headers['Location'] == '/challenges'
    assert QuizSession.query.filter(QuizSession.rematch_for_id.isnot(None)).count() == 0


def test_custom_topic_quizzes_offer_no_rematch(login, shared):
    take(login('Alice'), shared, answers(1, 3))
    QuizSession.query.update({QuizSession.topic: 'My biology notes'})
    app_module.db.session.commit()
    alice = login('Alice', create=False)
    assert 'Rematch' not in alice.get(f'/review/{attempt_id()}').get_data(as_text=True)
    alice.post(f'/rematch/{attempt_id()}')
    assert QuizSession.query.filter(QuizSession.rematch_for_id.isnot(None)).count() == 0


def test_rematches_survive_the_receiver_deleting_their_account(login, shared):
    alice = login('Alice')
    take(alice, shared, answers(1, 3))
    play_rematch(alice, attempt_id(), answers(3, 3))
    app_module.delete_player_data(User.query.filter_by(username='Phoenix').one())
    rematch = QuizSession.query.filter_by(user_id=User.query.filter_by(username='Alice').one().id,
                                          challenge_of_id=None).order_by(QuizSession.id.desc()).first()
    assert rematch.is_completed and rematch.rematch_for_id is None
