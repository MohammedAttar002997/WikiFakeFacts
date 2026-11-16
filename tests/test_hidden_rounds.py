"""Recognising a hidden round and leaving it out (hidden_rounds.py)."""
from hidden_rounds import fingerprint, has_hidden, visible_rounds

R0 = {'A.': True, 'B.': True, 'C.': True, 'D.': False}
R1 = {'E.': True, 'F.': False, 'G.': True, 'H.': True}
R2 = {'I.': False, 'J.': True, 'K.': True, 'L.': True}


def test_the_same_round_in_another_order_is_the_same_round():
    assert fingerprint(R0) == fingerprint(dict(reversed(list(R0.items()))))
    assert fingerprint(R0) != fingerprint(R1)


def test_hidden_rounds_are_left_out_with_their_explanations_and_quotes():
    rounds, explanations, quotes = visible_rounds([R0, R1, R2], ['x0', 'x1', 'x2'], ['q0', None, 'q2'],
                                                  {fingerprint(R1)})
    assert (rounds, explanations, quotes) == ([R0, R2], ['x0', 'x2'], ['q0', 'q2'])


def test_missing_quotes_and_short_lists_stay_that_way():
    rounds, explanations, quotes = visible_rounds([R0, R1, R2], ['x0'], None, {fingerprint(R0)})
    assert (rounds, explanations, quotes) == ([R1, R2], [], None)


def test_nothing_hidden_changes_nothing():
    assert visible_rounds([R0, R1], ['x0', 'x1'], None, set()) == ([R0, R1], ['x0', 'x1'], None)


def test_has_hidden():
    assert has_hidden([R0, R1], {fingerprint(R1)}) and not has_hidden([R0, R1], {fingerprint(R2)})
