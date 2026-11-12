"""The rules of a live match, tested on plain state with a made-up clock."""
from datetime import datetime, timedelta

import pytest

import live

T0 = datetime(2026, 11, 12, 12, 0, 0)
ROUND = {'A true.': True, 'B true.': True, 'C true.': True, 'D fake.': False}


def match(rounds=3):
    m = {'state': 'waiting', 'rounds': rounds, 'current_round': 0, 'round_started_at': None,
         'questions': [dict(ROUND) for _ in range(rounds)], 'answers': {'host': [], 'guest': []}}
    live.start(m, T0)
    return m


def at(seconds):
    return T0 + timedelta(seconds=live.COUNTDOWN_SECONDS + seconds)


def test_points_reward_being_right_and_fast():
    assert live.points_for(True, 0) == live.BASE_POINTS + live.SPEED_BONUS
    assert live.points_for(True, live.ROUND_SECONDS * 1000) == live.BASE_POINTS
    assert live.points_for(True, live.ROUND_SECONDS * 500) == live.BASE_POINTS + live.SPEED_BONUS // 2
    assert live.points_for(False, 0) == 0


def test_countdown_then_question():
    m = match()
    assert live.phase(m, T0)[0] == 'countdown'
    assert live.phase(m, at(0))[0] == 'question'
    assert live.phase(m, at(5))[1] == pytest.approx(live.ROUND_SECONDS - 5)


def test_round_closes_early_once_both_have_answered():
    m = match()
    live.record_answer(m, 'host', 0, 'D fake.', at(2))
    assert live.phase(m, at(3))[0] == 'question'  # still waiting for the guest
    live.record_answer(m, 'guest', 0, 'A true.', at(4))
    assert live.phase(m, at(4.5))[0] == 'reveal'


def test_reveal_then_next_round():
    m = match()
    live.record_answer(m, 'host', 0, 'D fake.', at(1))
    live.record_answer(m, 'guest', 0, 'D fake.', at(2))
    assert not live.advance(m, at(2 + live.REVEAL_SECONDS - 0.1))
    assert live.advance(m, at(2 + live.REVEAL_SECONDS))
    assert m['current_round'] == 1 and live.phase(m, at(2 + live.REVEAL_SECONDS))[0] == 'question'


def test_unanswered_rounds_time_out_and_the_match_still_ends():
    m = match(rounds=2)
    live.advance(m, at(10 * (live.ROUND_SECONDS + live.REVEAL_SECONDS)))  # nobody played, nobody polled
    assert m['state'] == 'finished'
    assert m['answers'] == {'host': [None, None], 'guest': [None, None]}


def test_answers_only_count_once_in_their_own_round_and_in_time():
    m = match()
    assert live.record_answer(m, 'host', 0, 'D fake.', T0) is None  # during the countdown
    assert live.record_answer(m, 'host', 1, 'D fake.', at(1)) is None  # not the current round
    assert live.record_answer(m, 'host', 0, 'Made up.', at(1)) is None  # not an option
    assert live.record_answer(m, 'host', 0, 'A true.', at(1))['points'] == 0
    assert live.record_answer(m, 'host', 0, 'D fake.', at(2)) is None  # already answered
    assert live.record_answer(m, 'guest', 0, 'D fake.', at(live.ROUND_SECONDS + 1)) is None  # too late


def test_faster_correct_answer_wins():
    m = match(rounds=1)
    live.record_answer(m, 'host', 0, 'D fake.', at(2))
    live.record_answer(m, 'guest', 0, 'D fake.', at(9))
    live.advance(m, at(30))
    assert m['state'] == 'finished' and live.winner(m) == 'host'
    assert live.totals(m)['guest']['correct'] == 1


def test_a_draw():
    m = match(rounds=1)
    live.advance(m, at(60))
    assert live.winner(m) is None


def test_an_invite_nobody_takes_expires():
    m = {'state': 'waiting', 'rounds': 3, 'current_round': 0, 'round_started_at': None, 'questions': [],
         'answers': {'host': [], 'guest': []}}
    assert not live.advance(m, T0 + timedelta(minutes=5), created_at=T0)
    assert live.advance(m, T0 + timedelta(minutes=live.WAITING_MINUTES + 1), created_at=T0)
    assert live.phase(m, T0)[0] == 'expired'


def test_scores_of_an_open_round_stay_hidden_until_the_reveal():
    m = match()
    live.record_answer(m, 'host', 0, 'D fake.', at(1))
    assert live.visible_totals(m, at(2))['host']['points'] == 0  # the guest is still answering
    live.record_answer(m, 'guest', 0, 'A true.', at(3))
    assert live.visible_totals(m, at(3.5))['host']['points'] > 0  # revealed
