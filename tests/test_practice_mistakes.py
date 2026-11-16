"""Practice your mistakes: rounds a player got wrong come back until they get them right, with no AI and no score."""
import json
import random

from app import db
from conftest import answers, play_quiz
from hidden_rounds import fingerprint
from models import HiddenRound, QuizSession, Score, UserBadge
from practice import choose, mistakes

R = [{f'S{i} a': True, f'S{i} FAKE': False, f'S{i} b': True, f'S{i} c': True} for i in range(8)]
RIGHT = [f'S{i} FAKE' for i in range(8)]
WRONG = [f'S{i} a' for i in range(8)]


def game(rounds, picks):
    return (rounds, picks, [f'why {i}' for i in range(len(rounds))], None)


# ---------- Rules (practice.py) -----------------------------------------------------------------------------------

def test_wrong_and_timed_out_rounds_are_mistakes():
    found = mistakes([game(R[:3], [RIGHT[0], WRONG[1], None])], [], set())
    assert [r for r, _, _ in found] == [R[1], R[2]]
    assert [e for _, e, _ in found] == ['why 1', 'why 2']


def test_newest_game_first_and_each_round_once():
    newest, older = game([R[3], R[1]], [WRONG[3], WRONG[1]]), game([R[1], R[0]], [WRONG[1], WRONG[0]])
    assert [r for r, _, _ in mistakes([newest, older], [], set())] == [R[3], R[1], R[0]]


def test_a_round_got_right_in_practice_is_done():
    practiced = game([R[1], R[2]], [RIGHT[1], WRONG[2]])  # Right once, wrong again once
    assert [r for r, _, _ in mistakes([game(R[:3], WRONG[:3])], [practiced], set())] == [R[0], R[2]]


def test_a_round_got_right_in_a_later_normal_game_is_still_offered_only_if_missed_somewhere():
    # Missing it once is enough to practice it; a correct answer in a normal game doesn't count as practice
    assert [r for r, _, _ in mistakes([game([R[0]], [RIGHT[0]]), game([R[0]], [WRONG[0]])], [], set())] == [R[0]]


def test_hidden_rounds_are_never_offered():
    assert mistakes([game(R[:2], WRONG[:2])], [], {fingerprint(R[0])})[0][0] == R[1]


def test_unanswered_rounds_of_a_short_answer_list_are_ignored():
    assert [r for r, _, _ in mistakes([game(R[:3], [WRONG[0]])], [], set())] == [R[0]]


def test_choose_takes_the_newest_few_in_a_shuffled_order():
    found = [(R[i], f'why {i}', f'q {i}') for i in range(8)]
    rounds, explanations, quotes = choose(found, n=5, rng=random.Random(1))
    assert sorted(rounds, key=R.index) == R[:5]
    assert [explanations[rounds.index(R[i])] for i in range(5)] == [f'why {i}' for i in range(5)]
    assert [quotes[rounds.index(R[i])] for i in range(5)] == [f'q {i}' for i in range(5)]


# ---------- In the app --------------------------------------------------------------------------------------------

def practice_once(client, picks_by_fake=True):
    """Start a practice quiz and answer every round right (or wrong); returns the practice QuizSession."""
    client.post('/practice/mistakes')
    quiz = QuizSession.query.filter_by(practice=True).order_by(QuizSession.id.desc()).first()
    for statements in json.loads(quiz.questions_json):
        pick = next(s for s, real in statements.items() if (real is False) == picks_by_fake)
        client.post('/quiz', data={'option': pick})
    return quiz


def test_history_offers_practice_only_after_a_mistake(login, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(3, 3))
    assert 'Practice your mistakes' not in client.get('/history').get_data(as_text=True)
    play_quiz(client, answers(1, 3))
    html = client.get('/history').get_data(as_text=True)
    assert 'Practice your mistakes' in html and '2 rounds you got wrong' in html


def test_practice_replays_the_missed_rounds_without_the_ai(ai, login, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(1, 3))
    missed = json.loads(QuizSession.query.one().questions_json)[1:]
    ai.quiz_calls.clear()
    quiz = practice_once(client)
    assert ai.quiz_calls == [] and quiz.practice and quiz.is_completed
    assert sorted(json.dumps(r) for r in json.loads(quiz.questions_json)) == sorted(json.dumps(r) for r in missed)


def test_practice_earns_no_score_or_badges(login, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(0, 3))
    scores, badges = Score.query.count(), UserBadge.query.count()
    practice_once(client)
    assert Score.query.count() == scores and UserBadge.query.count() == badges
    html = client.get('/results').get_data(as_text=True)
    assert "Practice doesn't count toward your score" in html and '/r/' not in html  # No share link


def test_rounds_got_right_dont_come_back(login, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(1, 3))
    practice_once(client, picks_by_fake=False)  # Wrong again: both still waiting
    assert '2 rounds you got wrong' in client.get('/history').get_data(as_text=True)
    practice_once(client)                        # Right this time: nothing left
    assert 'Practice your mistakes' not in client.get('/history').get_data(as_text=True)
    response = client.post('/practice/mistakes')
    assert response.headers['Location'].endswith('/history') and QuizSession.query.filter_by(practice=True).count() == 2


def test_hidden_rounds_are_left_out_of_practice(login, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(2, 3))
    last = json.loads(QuizSession.query.one().questions_json)[2]
    db.session.add(HiddenRound(fingerprint=fingerprint(last), topic='x', statements_json=json.dumps(last)))
    db.session.commit()
    assert 'Practice your mistakes' not in client.get('/history').get_data(as_text=True)


def test_results_of_a_normal_game_offer_practice(login, app_ctx):
    client = login('Ann')
    assert 'Practice your mistakes' in play_quiz(client, answers(2, 3))


def test_signed_out_practice_goes_home(app):
    assert app.test_client().post('/practice/mistakes').headers['Location'].endswith('/')
