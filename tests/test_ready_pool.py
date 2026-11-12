"""Which quizzes the refill makes ahead of time (ready_pool.py): only kinds players want, fairly, within the cap."""
from datetime import datetime, timedelta

from ready_pool import Kind, refill_plan

NOW = datetime(2026, 11, 12, 12, 0)
A = Kind('Animals', 'Easy', 3, 'English')
B = Kind('Space', 'Hard', 5, 'English')
C = Kind('Sports', 'Medium', 10, 'Deutsch (German)')


def wanted(*kinds):
    """Kinds wanted, the first one most recently."""
    return [(kind, NOW - timedelta(minutes=i)) for i, kind in enumerate(kinds)]


def test_every_wanted_kind_is_filled_to_the_target():
    assert sorted(refill_plan(wanted(A, B), {}, 0, cap=20, target=2)) == sorted([A, A, B, B])


def test_full_kinds_need_nothing():
    assert refill_plan(wanted(A, B), {A: 2, B: 1}, 0, cap=20, target=2) == [B]


def test_empty_kinds_get_one_before_any_kind_gets_a_second():
    assert refill_plan(wanted(A, B, C), {}, 0, cap=4, target=2) == [A, B, C, A]


def test_the_most_recently_wanted_kind_comes_first():
    assert refill_plan([(A, NOW - timedelta(days=2)), (B, NOW)], {}, 0, cap=1, target=2) == [B]


def test_the_daily_cap_counts_what_was_already_made():
    assert refill_plan(wanted(A, B), {}, 19, cap=20, target=2) == [A]
    assert refill_plan(wanted(A, B), {}, 20, cap=20, target=2) == []
    assert refill_plan(wanted(A, B), {}, 25, cap=20, target=2) == []


def test_nothing_wanted_means_nothing_made():
    assert refill_plan([], {A: 0}, 0) == []
