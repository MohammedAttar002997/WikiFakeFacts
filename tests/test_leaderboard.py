"""Leaderboard totals, filters, and which links it shows."""
import re
from datetime import datetime, timedelta

import pytest

from app import db
from models import Score, User, utcnow
from rating import rating

EASY, MEDIUM, HARD = 'Easy (Ages 7-12)', 'Medium (Ages 13-17)', 'Hard (Ages 18-99)'


@pytest.fixture
def scores(app_ctx):
    now = utcnow()
    rows = [
        ('Animals', 'Tiger', EASY, 9, timedelta(days=30)),
        ('Animals', 'Cat', EASY, 2, timedelta(minutes=1)),
        ('Space', 'Mars', HARD, 5, timedelta(days=3)),
        ('Space', 'Jupiter', HARD, 4, timedelta(minutes=2)),
        ('Custom', 'Volcanoes', MEDIUM, 7, timedelta(days=2)),
    ]
    users = {}
    for name in {r[0] for r in rows}:
        users[name] = User(username=name)
        db.session.add(users[name])
    db.session.commit()
    for name, topic, difficulty, score, age in rows:
        db.session.add(Score(user_id=users[name].id, topic=topic, difficulty=difficulty, score=score,
                             total_questions=10, percentage=score * 10, timestamp=now - age))
    db.session.commit()


def standings(client, query=''):
    html = client.get('/leaderboard' + query).get_data(as_text=True)
    rows = re.findall(r'class="rank-row">.*?(?:<a href="/players/\d+">|<a href="/analysis"[^>]*>)(\w+)<.*?<div class="pct">(\d+)',
                      html, re.S)
    return [(name, int(points)) for name, points in rows], html


# Each player's rating from their (difficulty, correct, questions) in the fixture
ANIMALS = round(rating([(EASY, 11, 20)]))
ANIMALS_WEEK = round(rating([(EASY, 2, 10)]))
SPACE = round(rating([(HARD, 9, 20)]))
CUSTOM = round(rating([(MEDIUM, 7, 10)]))


def test_one_row_per_player_ranked_by_rating(app, scores):
    # Animals answered the most right (11) but only on Easy, so it now ranks below Space (9 on Hard)
    assert standings(app.test_client())[0] == [('Space', SPACE), ('Custom', CUSTOM), ('Animals', ANIMALS)]


@pytest.mark.parametrize('query, expected', [
    ('?period=week', [('Space', SPACE), ('Custom', CUSTOM), ('Animals', ANIMALS_WEEK)]),
    ('?category=2', [('Animals', ANIMALS)]),
    ('?category=5', [('Space', SPACE)]),
    ('?category=custom', [('Custom', CUSTOM)]),
    ('?difficulty=3', [('Space', SPACE)]),
    ('?category=2&period=week', [('Animals', ANIMALS_WEEK)]),
])
def test_filters(app, scores, query, expected):
    assert standings(app.test_client(), query)[0] == expected


def test_no_matches_shows_reset(app, scores):
    _, html = standings(app.test_client(), '?category=2&difficulty=3')
    assert 'Show all scores' in html


def test_unknown_filter_values_fall_back_to_all(app, scores):
    rows, html = standings(app.test_client(), '?period=bogus&category=99&difficulty=x')
    assert rows == [('Space', SPACE), ('Custom', CUSTOM), ('Animals', ANIMALS)]
    assert 'Reset</a>' not in html


def test_only_own_entry_links_to_analysis(login, scores):
    html = login('Space', create=False).get('/leaderboard').get_data(as_text=True)
    assert '/analysis/' not in html
    assert '(you)' in html
