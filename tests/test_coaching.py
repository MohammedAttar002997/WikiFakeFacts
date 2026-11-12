"""The AI coach on My Progress waits for a few games and works from the facts the player actually missed;
the floating help button stays out of the way on phones."""
import json
import os
from types import SimpleNamespace

import data
from app import MIN_COACHING_GAMES, MISSED_PER_GAME, coaching_history, missed_rounds
from conftest import answers, play_quiz
from models import QuizSession, Score


# ---------- When the coach appears --------------------------------------------------------------------------------

def test_no_ai_call_before_enough_games(login, ai):
    client = login('Player')
    play_quiz(client, answers(2, 3))
    html = client.get('/analysis').get_data(as_text=True)
    assert ai.analysis_calls == []
    assert f'Play {MIN_COACHING_GAMES - 1} more games to unlock your personal AI coach.' in html
    assert 'Topic Breakdown' in html  # the plain statistics are still there


def test_one_game_to_go_says_so_in_the_singular(login, ai):
    client = login('Player')
    for _ in range(MIN_COACHING_GAMES - 1):
        play_quiz(client, answers(2, 3))
    assert 'Play 1 more game to unlock' in client.get('/analysis').get_data(as_text=True)


def test_coach_appears_once_there_are_enough_games(login, ai):
    client = login('Player')
    for _ in range(MIN_COACHING_GAMES):
        play_quiz(client, answers(2, 3))
    html = client.get('/analysis').get_data(as_text=True)
    assert len(ai.analysis_calls) == 1 and 'to unlock your personal AI coach' not in html


# ---------- What the coach is told --------------------------------------------------------------------------------

def finished_quiz(picks):
    questions = [{'Cats purr.': True, 'Cats are reptiles.': False}, {'Dogs bark.': True, 'Dogs fly.': False},
                 {'Owls hoot.': True, 'Owls are fish.': False}]
    return QuizSession(questions_json=json.dumps(questions), answers_json=json.dumps(picks))


def test_missed_rounds_name_the_fake_and_the_pick():
    quiz = finished_quiz(['Cats are reptiles.', 'Dogs bark.', None])
    assert missed_rounds(quiz) == [{'fake': 'Dogs fly.', 'picked': 'Dogs bark.'},
                                   {'fake': 'Owls are fish.', 'picked': None}]  # None: time ran out


def test_missed_rounds_are_capped_per_game():
    questions = [{f'True {i}.': True, f'Fake {i}.': False} for i in range(10)]
    quiz = QuizSession(questions_json=json.dumps(questions), answers_json=json.dumps([f'True {i}.' for i in range(10)]))
    assert len(missed_rounds(quiz)) == MISSED_PER_GAME


def test_the_coach_gets_the_missed_facts_of_each_game(login, ai, app_ctx, monkeypatch):
    seen = []
    monkeypatch.setattr('app.get_ai_analysis', lambda history, language: seen.append(json.loads(history)) or
                        ai.analysis(history, language))
    client = login('Player')
    for _ in range(MIN_COACHING_GAMES):
        play_quiz(client, answers(1, 3))  # one right, two missed
    client.get('/analysis')
    games = seen[0]
    assert len(games) == MIN_COACHING_GAMES and all(len(g['missed']) == 2 for g in games)
    assert all(m['fake'].endswith('FAKE') for g in games for m in g['missed'])


def test_scores_without_a_quiz_still_go_in(app_ctx):
    score = Score(topic='Mars', score=1, total_questions=3, percentage=33, difficulty='Easy (Ages 7-12)',
                  quiz_session_id=None, timestamp=__import__('models').utcnow())
    assert coaching_history([score])[0]['missed'] == []


# ---------- A short answer from the model -------------------------------------------------------------------------

def fake_openai(monkeypatch, reply):
    calls = []

    class Completions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=reply))])

    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(data, 'OpenAI', lambda api_key: client)
    monkeypatch.setattr(data, 'OPEN_AI_KEY', 'test-key')
    return calls


def test_long_answers_are_cut_to_what_the_page_shows(monkeypatch):
    insight = data.TopicInsight(topic='Mars', strength='s', weakness='w', recommendation='r')
    calls = fake_openai(monkeypatch, data.AIAnalysis(overall_summary='x', strongest_area='x', weakest_area='x',
                                                     learning_path=[f'step {i}' for i in range(10)],
                                                     topic_insights=[insight] * 6))
    analysis = data.get_ai_analysis('[]')
    assert len(analysis.learning_path) == data.MAX_PATH_STEPS and len(analysis.topic_insights) == data.MAX_INSIGHTS
    prompt = calls[0]['messages'][0]['content']
    assert 'Never guess' in prompt and "'missed'" in prompt


# ---------- Help button -------------------------------------------------------------------------------------------

def test_help_button_is_hidden_during_a_round(login):
    client = login('Player')
    assert 'class="chatbot-fab"' in client.get('/setup').get_data(as_text=True)
    client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    assert 'class="chatbot-fab"' not in client.get('/quiz').get_data(as_text=True)


def test_help_button_tucks_away_on_phones():
    static = os.path.join(os.path.dirname(__file__), '..', 'public', 'static')
    with open(os.path.join(static, 'css', 'app.css'), encoding='utf-8') as f:
        css = f.read()
    with open(os.path.join(static, 'js', 'app.js'), encoding='utf-8') as f:
        js = f.read()
    assert '.chatbot-fab.is-tucked' in css and "classList.toggle('is-tucked'" in js
