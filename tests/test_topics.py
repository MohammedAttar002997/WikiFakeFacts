"""More topics per category (Wikipedia's Vital articles, from topics.json) and the "Surprise me" category.
MENU, the hand-picked topics with a knowledge-base copy, stays as it was."""
import json
import os

import pytest

import app as app_module
from app import db
from default_text import ALL_TOPICS, CATEGORIES, MENU, TOPICS
from models import QuizSession, Score, User, utcnow

TOPICS_FILE = os.path.join(os.path.dirname(__file__), '..', 'topics.json')


# ---------- The topic lists ---------------------------------------------------------------------------------------

def test_every_category_keeps_its_menu_topics_first_and_gains_more():
    for category, menu_topics in MENU.items():
        assert TOPICS[category][:len(menu_topics)] == menu_topics
        assert len(TOPICS[category]) > len(menu_topics)


def test_topics_file_only_adds_new_topics_to_known_categories():
    with open(TOPICS_FILE, encoding='utf-8') as f:
        extra = json.load(f)
    assert set(extra) == set(CATEGORIES.values())  # every category, including those with no MENU topics
    menu_topics = {t for topics in MENU.values() for t in topics}
    for topics in extra.values():
        assert len(topics) == len(set(topics))  # no duplicates
        assert not set(topics) & menu_topics
        assert all(isinstance(t, str) and t.strip() == t and t for t in topics)


def test_knowledge_base_still_covers_exactly_the_menu():
    with open(os.path.join(os.path.dirname(__file__), '..', 'knowledge_base.json'), encoding='utf-8') as f:
        assert set(json.load(f)) == {t for topics in MENU.values() for t in topics}


# ---------- Starting a quiz ---------------------------------------------------------------------------------------

FORM = {'length': '1', 'difficulty': '2', 'language': 'en'}


def started_topic(client, category):
    client.post('/start_quiz', data={**FORM, 'category': category})
    return QuizSession.query.order_by(QuizSession.id.desc()).first()


def test_a_category_can_give_a_topic_beyond_the_menu(login, app_ctx, monkeypatch):
    monkeypatch.setattr(app_module.random, 'choice', lambda seq: seq[-1])
    quiz = started_topic(login('Player'), '2')
    assert quiz.topic == TOPICS['Animals'][-1] and quiz.topic not in MENU['Animals']


def test_surprise_me_picks_a_category_then_a_topic(login, app_ctx, monkeypatch):
    picks = []
    monkeypatch.setattr(app_module.random, 'choice', lambda seq: picks.append(list(seq)) or seq[0])
    quiz = started_topic(login('Player'), 'surprise')
    assert picks[0] == list(TOPICS)  # each category equally likely, however many topics it has
    assert quiz.topic == TOPICS[picks[0][0]][0]


def test_surprise_me_really_varies(login, app_ctx):
    client = login('Player')
    topics = {started_topic(client, 'surprise').topic for _ in range(8)}
    assert topics <= ALL_TOPICS and len(topics) > 1


def test_unknown_category_is_still_refused(login, app_ctx):
    client = login('Player')
    client.post('/start_quiz', data={**FORM, 'category': 'everything'})
    assert QuizSession.query.count() == 0


def test_setup_offers_surprise_me(login):
    html = login('Player').get('/setup').get_data(as_text=True)
    assert 'value="surprise"' in html and 'Surprise me' in html and 'Any category' in html


# ---------- New topics count as their category, not as custom content -------------------------------------------

@pytest.fixture
def score_on_new_topic(app_ctx):
    topic = TOPICS['Countries'][-1]
    user = User(username='Traveller')
    db.session.add(user)
    db.session.commit()
    db.session.add(Score(user_id=user.id, topic=topic, difficulty='Medium (Ages 13-17)', score=3,
                         total_questions=3, percentage=100, timestamp=utcnow()))
    db.session.commit()
    return topic


def test_leaderboard_files_new_topics_under_their_category(app, score_on_new_topic):
    countries = next(cid for cid, name in CATEGORIES.items() if name == 'Countries')
    client = app.test_client()
    assert 'Traveller' in client.get(f'/leaderboard?category={countries}').get_data(as_text=True)
    assert 'Traveller' not in client.get('/leaderboard?category=custom').get_data(as_text=True)


def test_new_topics_count_toward_the_explorer_badge():
    from badges import TOPIC_CATEGORY
    assert TOPIC_CATEGORY[TOPICS['Fruits'][-1]] == 'Fruits'


def test_new_topics_can_be_rematched_and_practised(app_ctx):
    assert app_module.can_rematch(QuizSession(topic=TOPICS['Technologies'][-1]))


def test_categories_without_menu_topics_get_theirs_from_the_topics_file():
    for category in ('History', 'Science', 'Famous People', 'Cities'):
        assert category in CATEGORIES.values() and category not in MENU
        assert len(TOPICS[category]) >= 100


def test_a_new_category_starts_a_quiz(login, app_ctx):
    history = next(cid for cid, name in CATEGORIES.items() if name == 'History')
    quiz = started_topic(login('Player'), history)
    assert quiz.topic in TOPICS['History']


def test_explorer_badge_needs_every_category():
    from badges import TOPIC_CATEGORY
    assert set(TOPIC_CATEGORY.values()) == set(CATEGORIES.values())


def test_weekly_events_stay_on_menu_categories(app_ctx):
    assert app_module.event_theme() in MENU  # their topics have a knowledge-base copy


def test_setup_shows_every_category(login):
    html = login('Player').get('/setup').get_data(as_text=True)
    assert all(f'id="cat{cid}"' in html for cid in CATEGORIES) and '🏛️' in html
