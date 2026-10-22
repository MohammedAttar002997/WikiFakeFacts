"""Quizzes survive double submits, odd AI output, a double-started daily challenge and hand-made forms."""
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

import app as app_module
import data
from app import db
from conftest import answers, play_quiz
from models import QuizSession, Score

FORM = {'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'}


# ---------- Answers sent twice ------------------------------------------------------------------------------------

@pytest.fixture
def racing(monkeypatch):
    """Make the next answer lose a race: another request answers the same round between this request loading the
    quiz and claiming the round (what a double click or the timer firing during a click does)."""
    real_claim = app_module.claim_round

    def claim_after_someone_else(quiz_session, round_index):
        db.session.execute(db.update(QuizSession).where(QuizSession.id == quiz_session.id)
                           .values(current_question_index=round_index + 1))
        db.session.commit()
        monkeypatch.setattr(app_module, 'claim_round', real_claim)  # Only the one answer races
        return real_claim(quiz_session, round_index)
    return lambda: monkeypatch.setattr(app_module, 'claim_round', claim_after_someone_else)


def test_the_losing_answer_changes_nothing(login, app_ctx, racing):
    client = login('Player')
    client.post('/start_quiz', data={**FORM, 'length': '2'})  # 5 rounds
    client.post('/quiz', data={'option': 'R0 FAKE'})
    racing()
    response = client.post('/quiz', data={'option': 'R1 FAKE'})
    assert response.headers['Location'] == '/quiz'
    quiz = QuizSession.query.one()
    assert quiz.score == 1 and len(app_module.json.loads(quiz.answers_json)) == 1  # Only the first answer


def test_last_round_answered_twice_saves_one_score(login, app_ctx, racing):
    client = login('Player')
    client.post('/start_quiz', data=FORM)  # 3 rounds
    for pick in ('R0 FAKE', 'R1 FAKE'):
        client.post('/quiz', data={'option': pick})
    client.post('/quiz', data={'option': 'R2 FAKE'})
    assert Score.query.count() == 1
    # And the database refuses a second score for the same quiz, whatever the code does
    quiz = QuizSession.query.one()
    db.session.add(Score(user_id=quiz.user_id, topic='x', score=1, total_questions=3, percentage=33,
                         quiz_session_id=quiz.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_claiming_a_round_only_works_once(login, app_ctx):
    client = login('Player')
    client.post('/start_quiz', data=FORM)
    quiz = QuizSession.query.one()
    assert app_module.claim_round(quiz, 0) is True
    db.session.commit()  # The quiz page commits the claim along with the answer
    assert app_module.claim_round(quiz, 0) is False
    assert quiz.current_question_index == 1


def test_results_wait_for_the_last_round(login, app_ctx):
    client = login('Player')
    client.post('/start_quiz', data=FORM)
    client.post('/quiz', data={'option': 'R0 FAKE'})
    assert client.get('/results').headers['Location'] == '/quiz'


# ---------- AI output -----------------------------------------------------------------------------------------------

def quiz_set(statements, explanation='Because.'):
    return data.QuizSet(questions=[data.QuizQuestion(statement=s, is_real=r) for s, r in statements],
                        explanation=explanation)


GOOD = [('A', True), ('B', True), ('C', True), ('D', False)]


@pytest.mark.parametrize('statements', [
    [('A', True), ('B', True), ('C', True), ('D', True)],      # no fake
    [('A', True), ('B', True), ('C', False), ('D', False)],    # two fakes
    [('A', True), ('A', True), ('C', True), ('D', False)],     # a repeated statement
    [('A', True), ('B', True), ('D', False)],                  # only three
    [('A', True), ('B', True), ('  ', True), ('D', False)],    # a blank one
])
def test_unplayable_rounds_are_dropped(statements):
    assert data.playable_rounds([quiz_set(statements), quiz_set(GOOD)]) == [
        ({'A': True, 'B': True, 'C': True, 'D': False}, 'Because.')]


@pytest.fixture
def openai_replies(monkeypatch):
    """Make the OpenAI client answer with the given parsed results in turn; returns the list of calls."""
    replies, calls = [], []

    class Completions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=replies.pop(0)))])

    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(data, 'OpenAI', lambda api_key: client)
    monkeypatch.setattr(data, 'OPEN_AI_KEY', 'test-key')
    monkeypatch.setattr(data, 'fetch_wikipedia_article', lambda title: ('Article text', 'Wikipedia'))
    return replies, calls


def test_a_bad_quiz_is_asked_for_again(openai_replies):
    replies, calls = openai_replies
    replies += [data.QuizData(topic='Cats', quiz_sets=[quiz_set(GOOD), quiz_set(GOOD[:3])]),
                data.QuizData(topic='Cats', quiz_sets=[quiz_set(GOOD), quiz_set(GOOD), quiz_set(GOOD)])]
    rounds, source, topic, explanations = data.get_ai_response(2, 'Cat')
    assert len(calls) == 2 and len(rounds) == 2 and len(explanations) == 2  # Extra rounds are left out


@pytest.mark.parametrize('reply', [None, data.QuizData(topic='Cats', quiz_sets=[])])
def test_unusable_output_fails_cleanly(openai_replies, reply):
    replies, calls = openai_replies
    replies += [reply, reply]
    with pytest.raises(ValueError, match='playable rounds'):
        data.get_ai_response(3, 'Cat')
    assert len(calls) == data.QUIZ_ATTEMPTS


def test_player_sees_a_friendly_message_when_the_ai_fails(login, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError('The AI returned only 1 playable rounds of 3')
    monkeypatch.setattr(app_module, 'get_ai_response', broken)
    client = login('Player')
    response = client.post('/start_quiz', data=FORM)
    assert response.headers['Location'] == '/setup'
    assert "couldn&#39;t create your quiz" in client.get('/setup').get_data(as_text=True)


# ---------- Daily challenge started twice -------------------------------------------------------------------------

def test_daily_challenge_started_twice_at_once_gives_one_attempt(login, app_ctx, monkeypatch):
    client = login('Player')
    client.post('/daily/start')
    first = QuizSession.query.one()
    # The second click checked for an attempt before the first one was saved
    monkeypatch.setattr(app_module, 'daily_attempt', lambda user, date, real=app_module.daily_attempt: (
        monkeypatch.setattr(app_module, 'daily_attempt', real) or None))
    response = client.post('/daily/start')
    assert response.headers['Location'] == '/quiz'
    assert QuizSession.query.count() == 1
    with client.session_transaction() as sess:
        assert sess['quiz_session_id'] == first.id


# ---------- Hand-made forms ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('changes', [{'category': '99'}, {'length': 'x'}, {'difficulty': ''}, {'category': None}])
def test_unknown_options_go_back_to_the_form(login, ai, changes):
    form = {k: v for k, v in {**FORM, **changes}.items() if v is not None}
    response = login('Player').post('/start_quiz', data=form)
    assert response.status_code == 302 and response.headers['Location'] == '/setup'
    assert ai.quiz_calls == []


def test_unknown_custom_quiz_options_go_back_to_the_form(login, ai):
    response = login('Player').post('/custom_quiz', data={'length': '7', 'difficulty': '2', 'language': 'en',
                                                          'url': 'https://example.com/v'})
    assert response.headers['Location'] == '/custom_quiz'
    assert ai.quiz_calls == []


def test_a_normal_quiz_still_works_end_to_end(login, app_ctx):
    play_quiz(login('Player'), answers(2, 3))
    assert (Score.query.one().score, QuizSession.query.one().is_completed) == (2, True)
