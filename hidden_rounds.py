"""Rounds the owner hid from /admin after a report: they're never served again (pure rules, no database).

A round is recognised by its four statements, whatever order they come in, so the same round is caught in a
daily challenge, a replay or the guest round. Fresh AI quizzes never repeat a round word for word, so they need no
check.
"""
import hashlib
import json


def fingerprint(statements):
    """A short id of a round ({statement: is_real}) that doesn't depend on the order of its statements."""
    text = json.dumps(sorted(statements), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]


def has_hidden(rounds, hidden):
    return any(fingerprint(r) in hidden for r in rounds)


def visible_rounds(rounds, explanations, quotes, hidden):
    """(rounds, explanations, quotes) without the hidden rounds, each list kept in step with the others. A list that
    is shorter than the rounds (or quotes being None) stays that way."""
    keep = [i for i, r in enumerate(rounds) if fingerprint(r) not in hidden]

    def pick(values):
        return None if values is None else [values[i] for i in keep if i < len(values)]
    return [rounds[i] for i in keep], pick(explanations), pick(quotes)
