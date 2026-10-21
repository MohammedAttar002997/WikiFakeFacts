"""Streak maths, badge rules, and awarding badges in the app."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import db
from badges import compute_streaks, earned_badge_keys
from conftest import answers, play_quiz
from default_text import MENU
from models import Score, User, UserBadge

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize('days_ago, expected', [
    ([], (0, 0)),
    ([0], (1, 1)),
    ([1, 2, 3], (3, 3)),          # ended yesterday: still alive
    ([2], (0, 1)),                # missed a day: broken
    ([0, 5, 6, 7, 8], (1, 4)),    # best is kept after a break
    ([0, 0, 1], (2, 2)),          # several quizzes on one day count once
])
def test_compute_streaks(days_ago, expected):
    assert compute_streaks([TODAY - timedelta(days=d) for d in days_ago], TODAY) == expected


def s(topic, score, total, difficulty='Medium (Ages 13-17)', day=0):
    return SimpleNamespace(topic=topic, score=score, total_questions=total, percentage=score / total * 100,
                           difficulty=difficulty, timestamp=datetime(2026, 10, 1) + timedelta(days=day))


def test_badge_rules():
    assert earned_badge_keys([], False) == set()
    assert earned_badge_keys([s('Cat', 1, 3)], False) == {'first_quiz'}
    assert 'perfect' not in earned_badge_keys([s('Cat', 3, 3)], False)  # needs 5+ rounds
    assert 'perfect' in earned_badge_keys([s('Cat', 5, 5)], False)
    assert 'hard_ace' in earned_badge_keys([s('Cat', 4, 5, 'Hard (Ages 18-99)')], False)
    assert 'streak_3' in earned_badge_keys([s('Cat', 1, 3, day=d) for d in range(3)], False)
    assert 'custom' in earned_badge_keys([s('My notes', 1, 3)], False)
    assert 'daily' in earned_badge_keys([s('Cat', 1, 3)], True)
    assert 'quiz_10' not in earned_badge_keys([s('Cat', 1, 3)] * 9, False)
    assert 'quiz_10' in earned_badge_keys([s('Cat', 1, 3)] * 10, False)


def test_comeback_needs_improvement_after_struggling():
    assert 'comeback' not in earned_badge_keys([s('Cat', 5, 5), s('Cat', 1, 5)], False)
    assert 'comeback' in earned_badge_keys([s('Cat', 1, 5), s('Cat', 4, 5)], False)


def test_explorer_needs_every_category():
    one_each = [s(topics[0], 1, 3) for topics in MENU.values()]
    assert 'explorer' not in earned_badge_keys(one_each[:-1], False)
    assert 'explorer' in earned_badge_keys(one_each, False)


def test_finishing_a_quiz_announces_new_badges_once(login, app_ctx):
    client = login('Player')
    html = play_quiz(client, answers(5, 5), length='2', difficulty='3')
    for name in ('First steps', 'Perfect round', 'Sharp mind'):
        assert name in html
    assert 'Badge unlocked' not in play_quiz(client, answers(5, 5), length='2', difficulty='3')
    user = User.query.filter_by(username='Player').one()
    assert sorted(b.badge for b in UserBadge.query.filter_by(user_id=user.id)) == ['first_quiz', 'hard_ace', 'perfect']


def test_my_progress_backfills_badges_and_shows_streak(login, app_ctx):
    client = login('Player')
    play_quiz(client, answers(1, 3))
    user = User.query.filter_by(username='Player').one()
    for days_ago in (1, 2):
        db.session.add(Score(user_id=user.id, topic='Cat', difficulty='Easy (Ages 7-12)', score=1, total_questions=3,
                             percentage=33.3, timestamp=datetime.utcnow() - timedelta(days=days_ago)))
    db.session.commit()
    html = client.get('/analysis').get_data(as_text=True)
    assert 'On a roll' in html and 'Badge unlocked' in html
    assert 'class="badge-tile"' in html and 'badge-tile is-locked' in html
    assert '🔥 3' in client.get('/players').get_data(as_text=True)
