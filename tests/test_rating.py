"""The leaderboard rating (rating.py), called directly."""
import pytest

from rating import rank, rating, wilson_lower

EASY, MEDIUM, HARD = 'Easy (Ages 7-12)', 'Medium (Ages 13-17)', 'Hard (Ages 18-99)'


def test_wilson_bound_is_cautious_with_few_answers():
    assert wilson_lower(1.0, 3) < 0.5  # 3 out of 3 says little
    assert wilson_lower(0.9, 50) > 0.75  # 45 out of 50 says a lot
    assert wilson_lower(0.5, 0) == 0
    assert 0 <= wilson_lower(0.0, 10) < 0.01


def test_a_lucky_streak_doesnt_beat_a_steady_record():
    assert rating([(HARD, 3, 3)]) < rating([(HARD, 45, 50)])


def test_playing_a_lot_badly_doesnt_beat_playing_well():
    """The old ranking (total correct) put this grinder first."""
    grinder = rating([(HARD, 60, 200)])  # 30%
    careful = rating([(HARD, 40, 50)])  # 80%
    assert careful > grinder


def test_harder_quizzes_count_for_more():
    assert rating([(HARD, 40, 50)]) > rating([(MEDIUM, 40, 50)]) > rating([(EASY, 40, 50)])
    assert rating([(HARD, 35, 50)]) > rating([(EASY, 50, 50)])  # perfect on Easy is worth at most half


def test_rating_grows_toward_accuracy_with_more_answers():
    values = [rating([(HARD, n * 8, n * 10)]) for n in (1, 3, 10, 100)]
    assert values == sorted(values) and values[-1] < 80 and values[-1] > 75


def test_old_difficulty_labels_count_as_medium():
    assert rating([('Medium', 40, 50)]) == rating([(MEDIUM, 40, 50)])


def test_rank_orders_and_adds_totals():
    players = rank([
        {'name': 'grinder', 'groups': [(EASY, 90, 200)]},
        {'name': 'careful', 'groups': [(HARD, 18, 20), (MEDIUM, 9, 10)]},
        {'name': 'new', 'groups': [(HARD, 3, 3)]},
    ])
    assert [p['name'] for p in players] == ['careful', 'new', 'grinder']
    assert players[0]['correct'] == 27 and players[0]['total_questions'] == 30
    assert players[0]['percentage'] == pytest.approx(90)


def test_equal_ratings_favour_more_answers():
    players = rank([{'name': 'a', 'groups': []}, {'name': 'b', 'groups': [(HARD, 0, 5)]}])
    assert [p['name'] for p in players] == ['b', 'a']
