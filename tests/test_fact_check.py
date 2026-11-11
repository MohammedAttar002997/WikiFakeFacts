"""Fact check: rounds made from real content are checked against it before anyone plays them, so a "fake" the
article actually backs (or a "real" fact it contradicts) doesn't mark a player wrong for knowing the topic."""
from types import SimpleNamespace

import pytest

import data

# Three real facts, then the fake
GOOD = {'Cats purr.': True, 'Cats have whiskers.': True, 'Cats sleep a lot.': True, 'Cats are reptiles.': False}
ALL_SUPPORTED_BUT_FAKE = ['supported', 'supported', 'supported', 'contradicted']


def round_of(n):
    return ({f'{s} ({n})': r for s, r in GOOD.items()}, f'Because {n}.', '')


# ---------- Judging one round -------------------------------------------------------------------------------------

@pytest.mark.parametrize('verdicts, quality', [
    (['supported', 'supported', 'supported', 'contradicted'], 2),   # as intended
    (['supported', 'supported', 'supported', 'not_in_text'], 2),    # the changed detail just isn't in the text
    (['supported', 'not_in_text', 'supported', 'contradicted'], 1),  # a real fact the text doesn't confirm
    (['supported', 'supported', 'supported', 'supported'], 0),      # the "fake" is true, as with the Milky Way's size
    (['supported', 'contradicted', 'supported', 'contradicted'], 0),  # a "real" fact is false: two lies
    (None, 1),                                                       # not checked
])
def test_round_quality(verdicts, quality):
    assert data.round_quality(GOOD, verdicts) == quality


def test_best_rounds_are_chosen_in_their_original_order():
    scored = [(0, 'wrong'), (2, 'a'), (1, 'b'), (2, 'c')]
    assert data.choose_rounds(scored, 2) == ['a', 'c']
    assert data.choose_rounds(scored, 3) == ['a', 'b', 'c']
    assert data.choose_rounds(scored, 4) == ['wrong', 'a', 'b', 'c']  # only when nothing else is left


# ---------- The check inside quiz generation ----------------------------------------------------------------------

@pytest.fixture
def openai(monkeypatch):
    """A fake OpenAI client. `quizzes` and `checks` are the replies to the quiz and the fact-check calls, in turn."""
    state = SimpleNamespace(quizzes=[], checks=[], calls=[])

    class Completions:
        def parse(self, **kwargs):
            state.calls.append(kwargs)
            if kwargs['response_format'] is data.QuizCheck:
                reply = state.checks.pop(0)
                if isinstance(reply, Exception):
                    raise reply
            else:
                reply = state.quizzes.pop(0)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=reply))])

    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(data, 'OpenAI', lambda api_key: client)
    monkeypatch.setattr(data, 'OPEN_AI_KEY', 'test-key')
    monkeypatch.setattr(data, 'fetch_wikipedia_article', lambda title: ('The article about cats.', 'Wikipedia'))
    return state


def quiz(*numbers):
    return data.QuizData(topic='Cats', quiz_sets=[
        data.QuizSet(questions=[data.QuizQuestion(statement=s, is_real=r) for s, r in round_of(n)[0].items()],
                     explanation=f'Because {n}.', source_quote='') for n in numbers])


def check(*verdict_lists):
    return data.QuizCheck(rounds=[data.RoundCheck(verdicts=v) for v in verdict_lists])


WRONG = ['supported'] * 4


def test_a_round_whose_fake_is_true_is_left_out(openai):
    openai.quizzes.append(quiz(1, 2, 3))
    openai.checks.append(check(ALL_SUPPORTED_BUT_FAKE, WRONG, ALL_SUPPORTED_BUT_FAKE))
    result = data.get_ai_response(2, 'Cat')
    assert [list(r)[0] for r in result.rounds] == ['Cats purr. (1)', 'Cats purr. (3)']
    assert result.explanations == ['Because 1.', 'Because 3.']  # explanations stay with their rounds


def test_the_checker_sees_the_content_but_not_which_statement_is_fake(openai):
    openai.quizzes.append(quiz(1, 2))
    openai.checks.append(check(ALL_SUPPORTED_BUT_FAKE, ALL_SUPPORTED_BUT_FAKE))
    data.get_ai_response(1, 'Cat')
    prompt = openai.calls[1]['messages'][1]['content']
    assert 'The article about cats.' in prompt and 'Cats are reptiles. (1)' in prompt
    assert 'fake' not in prompt.lower() and 'True' not in prompt


def test_too_many_wrong_rounds_ask_again_and_keep_the_good_ones(openai):
    openai.quizzes += [quiz(1, 2, 3), quiz(4, 5, 6)]
    openai.checks += [check(ALL_SUPPORTED_BUT_FAKE, WRONG, WRONG), check(WRONG, ALL_SUPPORTED_BUT_FAKE, WRONG)]
    result = data.get_ai_response(2, 'Cat')
    assert [list(r)[0] for r in result.rounds] == ['Cats purr. (1)', 'Cats purr. (5)']


def test_a_quiz_is_never_lost_to_the_check(openai):
    """If both tries are mostly wrong, the quiz is still played rather than failing."""
    openai.quizzes += [quiz(1, 2), quiz(3, 4)]
    openai.checks += [check(WRONG, WRONG), check(WRONG, WRONG)]
    assert len(data.get_ai_response(3, 'Cat').rounds) == 3


@pytest.mark.parametrize('reply', [RuntimeError('API down'), None, 'wrong shape'])
def test_a_failed_check_plays_the_rounds_unchecked(openai, reply):
    openai.quizzes.append(quiz(1, 2, 3))
    openai.checks.append(check(ALL_SUPPORTED_BUT_FAKE) if reply == 'wrong shape' else reply)
    assert len(data.get_ai_response(2, 'Cat').rounds) == 2
    assert len(openai.calls) == 2  # no second quiz needed


def test_custom_content_is_checked_against_itself(openai):
    openai.quizzes.append(quiz(1, 2))
    openai.checks.append(check(ALL_SUPPORTED_BUT_FAKE, ALL_SUPPORTED_BUT_FAKE))
    data.get_ai_response(1, '', custom_content='My notes about cats.')
    assert 'My notes about cats.' in openai.calls[1]['messages'][1]['content']


def test_quizzes_from_the_models_own_knowledge_are_not_checked(openai, monkeypatch):
    monkeypatch.setattr(data, 'fetch_wikipedia_article', lambda title: ('', ''))
    monkeypatch.setattr(data, 'fetch_local_knowledge', lambda title: ('', ''))
    openai.quizzes.append(quiz(1, 2))
    data.get_ai_response(1, 'Cat')
    assert len(openai.calls) == 1  # there's no text to check against
