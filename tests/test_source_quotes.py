"""Source quotes: the sentence of the article behind each round's answer, shown only when it's really in there."""
from types import SimpleNamespace

import pytest

import data
from app import db
from conftest import answers, play_quiz
from models import QuizSession

ARTICLE = ("The cheetah is a large cat native to Africa and central Iran.\n\n"
           "It is the fastest land animal, capable of running at 80 to 98 km/h.")
REAL = [('Cheetahs live in Africa.', True), ('Cheetahs are cats.', True), ('Cheetahs are fast.', True),
        ('Cheetahs are the slowest land animal.', False)]


# ---------- Checking the AI's quote against the content -------------------------------------------------------------

@pytest.mark.parametrize('quote, expected', [
    ('It is the fastest land animal, capable of running at 80 to 98 km/h.',
     'It is the fastest land animal, capable of running at 80 to 98 km/h.'),
    ('“it is the FASTEST land animal,   capable of running”', 'it is the FASTEST land animal,   capable of running'),
    ('The cheetah is a large cat native to Africa and central Iran. It is the fastest',  # across a paragraph break
     'The cheetah is a large cat native to Africa and central Iran. It is the fastest'),
    ('It is the fastest land animal.', 'It is the fastest land animal'),  # the article has a comma there
    ('The cheetah is a large cat native to central Iran.', None),  # dropped words change the sentence
    ('Cheetahs are the fastest animals on land.', None),  # a paraphrase, not a quote
    ('fastest land', None),  # too short to mean anything
    ('', None),
])
def test_only_quotes_really_in_the_content_are_kept(quote, expected):
    assert data.verified_quote(quote, data.normalize_text(ARTICLE)) == expected


@pytest.fixture
def openai_reply(monkeypatch):
    """The OpenAI client answers with one round per quote given; returns a function that sets them."""
    reply = {}

    class Completions:
        def parse(self, **kwargs):
            reply['prompt'] = kwargs['messages'][1]['content']
            sets = [data.QuizSet(questions=[data.QuizQuestion(statement=f'{s} ({i})', is_real=r) for s, r in REAL],
                                 explanation='Because.', source_quote=q) for i, q in enumerate(reply['quotes'])]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                parsed=data.QuizData(topic='Cheetah', quiz_sets=sets)))])

    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(data, 'OpenAI', lambda api_key: client)
    monkeypatch.setattr(data, 'OPEN_AI_KEY', 'test-key')
    monkeypatch.setattr(data, 'fetch_wikipedia_article', lambda title: (ARTICLE, 'Wikipedia'))

    def set_quotes(*quotes):
        reply['quotes'] = list(quotes)
        return reply
    return set_quotes


def test_wikipedia_quizzes_get_checked_quotes_and_the_article_link(openai_reply):
    reply = openai_reply('It is the fastest land animal, capable of running at 80 to 98 km/h.',
                         'Cheetahs are the fastest animals on land.', '')
    quiz = data.get_ai_response(2, 'Cheetah')
    assert quiz.quotes == ['It is the fastest land animal, capable of running at 80 to 98 km/h.', None]
    assert quiz.source_url == 'https://en.wikipedia.org/wiki/Cheetah'
    assert "'source_quote'" in reply['prompt'] and 'not translated' in reply['prompt']


def test_article_titles_become_wikipedia_links(openai_reply):
    openai_reply('', '')
    assert data.get_ai_response(1, 'Grand Piano').source_url == 'https://en.wikipedia.org/wiki/Grand_Piano'
    assert data.wikipedia_url('Tennis/Squash') == 'https://en.wikipedia.org/wiki/Tennis/Squash'
    assert data.wikipedia_url('Rock & Roll') == 'https://en.wikipedia.org/wiki/Rock_%26_Roll'


def test_custom_content_quotes_are_checked_against_it_and_have_no_link(openai_reply):
    openai_reply('Volcanoes erupt magma when pressure builds.', 'It is the fastest land animal, capable of running')
    quiz = data.get_ai_response(2, 'Notes', custom_content='Volcanoes erupt magma when pressure builds. ' * 3)
    assert quiz.quotes == ['Volcanoes erupt magma when pressure builds.', None]
    assert quiz.source_url is None


def test_quizzes_from_the_models_own_knowledge_have_no_quotes(openai_reply, monkeypatch):
    monkeypatch.setattr(data, 'fetch_wikipedia_article', lambda title: ('', ''))
    monkeypatch.setattr(data, 'fetch_local_knowledge', lambda title: ('', ''))
    openai_reply('The topic is Cheetah. Use your internal knowledge to generate the quiz.')
    quiz = data.get_ai_response(1, 'Cheetah')
    assert quiz.quotes == [None] and quiz.source_url is None


# ---------- Showing them ----------------------------------------------------------------------------------------------

def test_results_show_each_rounds_quote_and_the_article(login, app_ctx):
    html = play_quiz(login('Player'), answers(1, 3))
    assert 'Quote proving round 0.' in html and 'Quote proving round 2.' in html
    quiz = QuizSession.query.one()
    assert f'href="{quiz.source_url}"' in html and 'Read the full article on Wikipedia' in html
    assert 'lang="en"' in html  # English Wikipedia quotes, whatever the page's language


def test_review_page_shows_quotes_too(login, app_ctx):
    client = login('Player')
    play_quiz(client, answers(1, 3))
    html = client.get(f'/review/{QuizSession.query.one().id}').get_data(as_text=True)
    assert 'Quote proving round 1.' in html and 'From the Wikipedia article' in html


def test_challenge_replays_and_the_daily_challenge_keep_the_quotes(login, app_ctx):
    play_quiz(login('Phoenix'), answers(1, 3))
    code = QuizSession.query.one().share_code
    alice = login('Alice')
    alice.post(f'/r/{code}/play')
    attempt = QuizSession.query.filter(QuizSession.challenge_of_id.isnot(None)).one()
    original = QuizSession.query.filter_by(share_code=code).one()
    assert (attempt.quotes_json, attempt.source_url) == (original.quotes_json, original.source_url)

    alice.post('/daily/start')
    daily = QuizSession.query.filter(QuizSession.daily_challenge_id.isnot(None)).one()
    assert daily.quotes_json and daily.source_url


def test_custom_quiz_quotes_say_where_they_are_from(login, app_ctx):
    client = login('Player')
    client.post('/custom_quiz', data={'length': '1', 'difficulty': '2', 'language': 'en', 'title': '',
                                      'url': 'https://example.com/volcanoes'})
    for pick in answers(1, 3):
        client.post('/quiz', data={'option': pick})
    html = client.get('/results').get_data(as_text=True)
    assert 'Quote proving round 0.' in html and "From the quiz's source text" in html
    assert 'wikipedia.org' not in html


def test_quizzes_from_before_quotes_still_review_fine(login, app_ctx):
    client = login('Player')
    play_quiz(client, answers(1, 3))
    quiz = QuizSession.query.one()
    quiz.quotes_json = quiz.source_url = None
    db.session.commit()
    html = client.get(f'/review/{quiz.id}').get_data(as_text=True)
    assert 'Answer review' in html and 'source-quote' not in html and 'wikipedia.org' not in html
