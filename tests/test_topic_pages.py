"""Public topic pages: a sample round for every built-in topic, the index, the sitemap and robots.txt."""
import json
from datetime import timedelta

import pytest

import topic_pages as rules
from app import db
from hidden_rounds import fingerprint
from models import DailyChallenge, HiddenRound, QuizSession, User, utcnow
from views import topics as views

TOPIC = 'Eiffel Tower' if 'Eiffel Tower' in views.SLUG_OF else sorted(views.SLUG_OF)[0]
SLUG = views.SLUG_OF[TOPIC]
ROUND = {'It is in Paris.': True, 'It opened in 1889.': True, 'It is made of iron.': True,
         'It is painted green.': False}


# ---------- Rules (topic_pages.py) --------------------------------------------------------------------------------

@pytest.mark.parametrize('title, slug', [('Eiffel Tower', 'eiffel-tower'), ('Côte d’Ivoire', 'cote-divoire'), ("Côte d'Ivoire", 'cote-divoire'),
                                         ('C++', 'c'), ('  World War II ', 'world-war-ii'), ('!!!', '')])
def test_slugify(title, slug):
    assert rules.slugify(title) == slug


def test_slug_map_keeps_the_first_of_a_clash_and_drops_empty_slugs():
    assert rules.slug_map(['C++', 'C', '???']) == {'c': 'C'}


def test_every_built_in_topic_has_its_own_page():
    from default_text import ALL_TOPICS
    assert set(views.SLUGS.values()) == ALL_TOPICS


def test_playable_rounds_skip_broken_and_hidden_ones():
    two_fakes = {'a': True, 'b': False, 'c': False, 'd': True}
    other = {'w': True, 'x': True, 'y': True, 'z': False}
    rounds = [ROUND, two_fakes, {'a': True}, other]
    assert [i for i, _ in rules.playable_rounds(rounds, {fingerprint(other)})] == [0]


def test_pick_is_steady_for_a_seed_and_none_without_rounds():
    candidates = [('a', 1), ('b', 2), ('c', 3)]
    assert rules.pick(candidates, 'x:2026-11-18') == rules.pick(candidates, 'x:2026-11-18')
    assert {rules.pick(candidates, f'x:{d}') for d in range(30)} == set(candidates)
    assert rules.pick([], 'x') is None


# ---------- The page ----------------------------------------------------------------------------------------------

def played(topic=TOPIC, rounds=(ROUND,), source_url='https://en.wikipedia.org/wiki/Eiffel_Tower', **extra):
    user = User.query.first() or User(username='Ann')
    db.session.add(user)
    db.session.commit()
    quiz = QuizSession(user_id=user.id, topic=topic, questions_json=json.dumps(list(rounds)),
                       explanations_json=json.dumps(['It was never green.'] * len(rounds)),
                       source_url=source_url, total_questions=len(rounds), current_question_index=len(rounds),
                       is_completed=True, **extra)
    db.session.add(quiz)
    db.session.commit()
    return quiz


def test_a_topic_with_a_played_round_offers_it_to_anyone(app, app_ctx):
    played()
    html = app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)
    assert f'<h1>{TOPIC}</h1>' in html and 'It is painted green.' in html and 'name="option"' in html
    assert 'rel="canonical"' in html and 'noindex' not in html
    assert 'is painted green' not in html.split('name="option"')[0].split('<form')[0].split('</h1>')[1]


def test_answering_shows_the_review_without_an_account(app, app_ctx):
    played()
    guest = app.test_client()
    guest.get(f'/topic/{SLUG}')
    html = guest.post(f'/topic/{SLUG}', data={'option': 'It is in Paris.'}).get_data(as_text=True)
    assert 'Answer review' in html and 'It was never green.' in html and 'Missed' in html


def test_the_answer_is_checked_against_the_round_shown(app, app_ctx):
    played()
    guest = app.test_client()
    assert guest.post(f'/topic/{SLUG}', data={'option': 'It is in Paris.'}).headers['Location'].endswith(SLUG)
    guest.get(f'/topic/{SLUG}')
    assert guest.post(f'/topic/{SLUG}', data={'option': 'Made up'}).headers['Location'].endswith(SLUG)


def test_without_a_round_the_page_asks_search_engines_not_to_list_it(app):
    html = app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)
    assert 'Nobody has played' in html and '<meta name="robots" content="noindex">' in html


@pytest.mark.parametrize('extra', [{'source_url': None}, {'practice': True}])
def test_uploads_and_practice_are_never_samples(app, app_ctx, extra):
    played(**{'source_url': 'https://en.wikipedia.org/wiki/X', **extra})
    assert 'Nobody has played' in app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)


def test_past_daily_challenges_are_samples_but_not_todays(app, app_ctx):
    db.session.add(DailyChallenge(date=utcnow().strftime('%Y-%m-%d'), language='English', topic=TOPIC,
                                  questions_json=json.dumps([ROUND]), explanations_json='["Why."]'))
    db.session.commit()
    assert 'Nobody has played' in app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)
    db.session.add(DailyChallenge(date=(utcnow() - timedelta(days=2)).strftime('%Y-%m-%d'), language='English',
                                  topic=TOPIC, questions_json=json.dumps([ROUND]), explanations_json='["Why."]'))
    db.session.commit()
    assert 'It is painted green.' in app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)


def test_hidden_rounds_are_never_samples(app, app_ctx):
    played()
    db.session.add(HiddenRound(fingerprint=fingerprint(ROUND), topic=TOPIC, statements_json=json.dumps(ROUND)))
    db.session.commit()
    assert 'Nobody has played' in app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)


def test_other_spellings_redirect_and_unknown_topics_are_404(app):
    client = app.test_client()
    response = client.get(f'/topic/{SLUG.upper()}')
    assert response.status_code == 301 and response.headers['Location'].endswith(f'/topic/{SLUG}')
    assert client.get('/topic/no-such-topic-anywhere').status_code == 404


def test_the_full_quiz_button(app, login):
    assert 'href="/"' in app.test_client().get(f'/topic/{SLUG}').get_data(as_text=True)
    html = login('Ann').get(f'/topic/{SLUG}').get_data(as_text=True)
    assert '/setup?topic=' in html
    setup = login('Ann').get(f'/setup?topic={TOPIC}').get_data(as_text=True)
    assert 'id="catTopic" value="topic" required checked' in setup and f'value="{TOPIC}"' in setup


# ---------- Index, sitemap, robots --------------------------------------------------------------------------------

def test_the_index_lists_categories_and_their_topics(app):
    from badges import TOPIC_CATEGORY
    client = app.test_client()
    category = TOPIC_CATEGORY[TOPIC]
    assert f'/topics/{rules.slugify(category)}' in client.get('/topics').get_data(as_text=True)
    assert f'/topic/{SLUG}' in client.get(f'/topics/{rules.slugify(category)}').get_data(as_text=True)
    assert client.get('/topics/no-such-category').status_code == 404


def test_the_sitemap_lists_only_topics_with_rounds(app, app_ctx, monkeypatch):
    monkeypatch.setitem(views._sitemap_cache, 'topics', None)
    xml = app.test_client().get('/sitemap.xml').get_data(as_text=True)
    assert '/topics</loc>' in xml and f'/topic/{SLUG}<' not in xml
    played()
    monkeypatch.setitem(views._sitemap_cache, 'topics', None)
    response = app.test_client().get('/sitemap.xml')
    assert response.mimetype == 'application/xml' and f'/topic/{SLUG}</loc>' in response.get_data(as_text=True)


def test_robots_keeps_private_pages_out_and_names_the_sitemap(app):
    text = app.test_client().get('/robots.txt').get_data(as_text=True)
    assert 'Disallow: /account' in text and 'Disallow: /admin' in text and 'Sitemap: http' in text
    assert 'Disallow: /topic' not in text


def test_the_footer_links_the_topics(app):
    assert 'href="/topics"' in app.test_client().get('/').get_data(as_text=True)
